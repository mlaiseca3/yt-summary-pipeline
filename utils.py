import os
import re
import requests
from google import genai
from google.genai import types
import json
from urllib.parse import urlparse, parse_qs
from youtube_transcript_api.formatters import JSONFormatter
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)


def extract_video_title(video_id: str) -> str:
    youtube_api_url = "https://www.googleapis.com/youtube/v3/videos"
    params = {"part": "snippet", "id": video_id, "key": os.getenv("GOOGLE_APIS_KEY")}
    resp = requests.get(youtube_api_url, params=params)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items", [])
    if items:
        title = items[0]["snippet"]["title"]
        return title
    else:
        print("Video not found or is private/deleted.")


def extract_channel_title(video_id: str) -> str:
    youtube_api_url = "https://www.googleapis.com/youtube/v3/videos"
    params = {"part": "snippet", "id": video_id, "key": os.getenv("GOOGLE_APIS_KEY")}
    resp = requests.get(youtube_api_url, params=params)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items", [])
    if items:
        title = items[0]["snippet"]["channelTitle"]
        return title
    else:
        print("Video not found or is private/deleted.")


def extract_channel_id(video_id: str) -> str:
    youtube_api_url = "https://www.googleapis.com/youtube/v3/videos"
    params = {"part": "snippet", "id": video_id, "key": os.getenv("GOOGLE_APIS_KEY")}
    resp = requests.get(youtube_api_url, params=params)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items", [])
    if items:
        channel_id = items[0]["snippet"]["channelId"]
        return channel_id
    else:
        print("Video not found or is private/deleted.")


def get_transcript(video_id: str) -> str:
    """Fetch the transcript text for a video, preferring English."""
    ytt_api = YouTubeTranscriptApi()
    try:
        transcript_list = ytt_api.list(video_id)
        try:
            transcript = transcript_list.find_transcript(["en"])
        except NoTranscriptFound:
            # Fall back to whatever's available, translating to English if possible.
            transcript = next(iter(transcript_list))
            if transcript.is_translatable:
                transcript = transcript.translate("en")
        fetched = transcript.fetch()

    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable) as e:
        raise RuntimeError(f"Could not get a transcript for this video: {e}")

    # fetched is a FetchedTranscript of snippet objects (each with a .text attribute)
    return " ".join(snippet.text for snippet in fetched)


def summarize_transcript(transcript_text: str) -> dict:
    """Summarize the transcript using the Gemini API."""
    
    SUMMARY_SCHEMA = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "A concise 3-5 sentence overview of what the video covers.",
            },
            "key_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The main arguments or takeaways. Substance, not filler.",
            },
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Distinct subjects/themes touched on, ordered by how much time was spent on each.",
            },
            "references": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific people, books, tools, papers, products, studies, or sources named or cited. Empty list if none.",
            },
            "further_research": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Follow-up questions or areas worth digging into further, based on gaps or things mentioned but not fully explained.",
            },
        },
        "required": ["summary", "key_points", "topics", "references", "further_research"],
    }
    
    client = genai.Client()

    prompt = (
        "Analyze the following YouTube video transcript and extract a structured "
        "breakdown: an overview summary, key points, topics covered, any references "
        "mentioned (people, books, tools, papers, products, studies, sources), and "
        "further research directions a curious viewer might pursue.\n\n"
        f"Transcript:\n{transcript_text}"
    )

    model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    print(f"Using model: {model_name} for summarization...")

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SUMMARY_SCHEMA,
        ),
    )
    return json.loads(response.text)


def extract_video_id(url: str) -> str:
    """Pull the video ID out of common YouTube URL formats."""
    parsed = urlparse(url)
    
    # https://www.youtube.com/watch?v=VIDEO_ID
    if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            qs = parse_qs(parsed.query)

            if "v" in qs:
                return qs["v"][0]
        # https://www.youtube.com/shorts/VIDEO_ID or /embed/VIDEO_ID
        match = re.match(r"^/(shorts|embed|live)/([^/?]+)", parsed.path)
        if match:
            return match.group(2)

    # https://youtu.be/VIDEO_ID
    if parsed.hostname in ("youtu.be",):
        url_id = parsed.path.lstrip("/")
        url_id = url_id.rstrip("\\")
        return url_id

    raise ValueError(f"Could not extract a video ID from URL: {url}")

def summarize_video(video_id: str, video_url: str = None) -> dict:
    """Fetch a video's transcript and return the standard summary dict for it."""
    transcript_text = get_transcript(video_id)
    result = summarize_transcript(transcript_text)
    result["video_id"] = video_id
    result["video_url"] = video_url or f"https://www.youtube.com/watch?v={video_id}"
    result["video_title"] = extract_video_title(video_id)
    result["channel_id"] = extract_channel_id(video_id)
    result["channel_title"] = extract_channel_title(video_id)
    return result


def save_summary_to_json(video_url: str, output_dir: str = "output") -> None:
    """Save the summary dictionary to a JSON file in the specified output directory."""
    VIDEO_ID = extract_video_id(video_url)
    folder_path = output_dir
    file_path = os.path.join(folder_path, f"{VIDEO_ID}.json")
    os.makedirs(folder_path, exist_ok=True)
    print("Transcript fetched. Now summarizing with Gemini and saving to JSON...")
    result = summarize_video(VIDEO_ID, video_url)

    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(result, file, indent=4)

def get_channel_uploads_playlist_id(channel_id: str) -> str:
    """Get the uploads playlist ID for a given channel ID."""
    return "UU" + channel_id[2:]

def get_all_channel_video_ids(channel_id: str) -> list:
    """Get all video IDs from a channel's uploads playlist."""
    playlist_id = get_channel_uploads_playlist_id(channel_id)
    ids, page_token = [], None
    while True:
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
            "key": os.getenv("GOOGLE_APIS_KEY")
        }
        if page_token:
            params["pageToken"] = page_token
        r = requests.get("https://www.googleapis.com/youtube/v3/playlistItems", params=params)
        r.raise_for_status()
        data = r.json()
        ids += [item["contentDetails"]["videoId"] for item in data["items"]]
        page_token = data.get("nextPageToken")
        if not page_token:
            return ids

def save_all_channel_video_ids_to_json(channel_id: str, output_dir: str = "output") -> None:
    """Save all video IDs from a channel's uploads playlist to a JSON file."""
    video_ids = get_all_channel_video_ids(channel_id)
    output_dict = {
        "channel_id": channel_id,
        "channel_name": extract_channel_title(video_ids[0]) if video_ids else "Unknown",
        "video_ids": video_ids,
    }
    folder_path = output_dir
    file_path = os.path.join(folder_path, f"{channel_id}_video_ids.json")
    os.makedirs(folder_path, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(output_dict, file, indent=4)


def sanitize_folder_name(name: str) -> str:
    """Strip characters that aren't safe to use in a filesystem folder name."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def summarize_channel_videos(channel_id: str, output_dir: str = "output") -> str:
    """Summarize every video uploaded by a channel into a channel-named folder.

    Uses get_all_channel_video_ids() to enumerate the channel's uploads
    playlist, then summarize_video() to build the standard summary JSON for
    each video.
    """
    video_ids = get_all_channel_video_ids(channel_id)
    if not video_ids:
        print("No videos found for this channel.")
        return None

    channel_title = extract_channel_title(video_ids[0]) or "Unknown"
    folder_name = sanitize_folder_name(f"{channel_id}_{channel_title}")
    folder_path = os.path.join(output_dir, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    for index, video_id in enumerate(video_ids, start=1):
        print(f"[{index}/{len(video_ids)}] Summarizing {video_id}...")
        try:
            result = summarize_video(video_id)
        except RuntimeError as e:
            print(f"Skipping {video_id}: {e}")
            continue

        file_path = os.path.join(folder_path, f"{video_id}.json")
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(result, file, indent=4)

    print(f"Saved summaries for {len(video_ids)} video(s) to {folder_path}")
    return folder_path
