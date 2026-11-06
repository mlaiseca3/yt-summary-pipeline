import os
import re
import requests
import time
import random
import yt_dlp
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
    IpBlocked,
    RequestBlocked,
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


def _clean_vtt(vtt_text: str) -> str:
    """Remove VTT headers, timestamps, and styling tags to get plain text."""
    lines = vtt_text.splitlines()
    plain_text = []
    # Skip VTT header (usually starts with "WEBVTT")
    start_index = 0
    for i, line in enumerate(lines):
        if "Kind:" in line or "Language:" in line:
            start_index = i + 1
            break
    
    # If no header found, just start from the beginning
    # But wait, VTT usually has "WEBVTT" at line 0.
    
    for line in lines[start_index:]:
        # Skip timestamps (e.g., 00:00:00.000 --> 00:00:05.000)
        if "-->" in line:
            continue
        # Skip empty lines and VTT header line
        if not line.strip() or line.strip() == "WEBVTT":
            continue
        # Remove HTML-like tags (e.g., <c>Text</c>)
        clean_line = re.sub(r'<[^>]*>', '', line)
        if clean_line.strip():
            plain_text.append(clean_line.strip())
            
    return " ".join(plain_text)


def fetch_transcript_with_yt_dlp(video_id: str) -> str:
    """Fallback: Fetch transcript using yt-dlp by extracting caption URLs."""
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        
        # Try automatic captions first, then manual subtitles
        captions = info.get('automatic_captions') or info.get('subtitles')
        if not captions:
            raise RuntimeError("No captions available via yt-dlp")
        
        # Prefer English
        en_captions = captions.get('en')
        if not en_captions:
            # Fallback to any available caption
            first_lang = next(iter(captions), None)
            if not first_lang:
                raise RuntimeError("No captions available in any language")
            en_captions = captions[first_lang]
        
        # en_captions is usually a list of formats; take the first one
        caption_url = en_captions[0]['url'] if isinstance(en_captions, list) else en_captions[0]['url']
        
        resp = requests.get(caption_url)
        resp.raise_for_status()
        return _clean_vtt(resp.text)


def get_transcript(video_id: str) -> str:
    """Fetch the transcript text for a video, preferring English.
    Tries youtube-transcript-api first, then falls back to yt-dlp.
    """
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
        return " ".join(snippet.text for snippet in fetched)

    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable, IpBlocked, RequestBlocked) as e:
        # If it's just disabled or unavailable, yt-dlp won't help
        if isinstance(e, (TranscriptsDisabled, VideoUnavailable)):
            raise RuntimeError(f"Could not get a transcript for this video: {e}")
        
        # For IP blocks, request blocks, or "No Transcript Found" (which might be a block in disguise), try yt-dlp
        print(f"youtube-transcript-api failed ({e}), attempting yt-dlp fallback...")
        try:
            return fetch_transcript_with_yt_dlp(video_id)
        except Exception as ydl_e:
            raise RuntimeError(f"Both transcript methods failed. Final error: {ydl_e}")


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

def resolve_channel_id(channel_input: str) -> str:
    """Resolve a channel URL, handle, or ID to a channel ID (starting with UC)."""
    # 1. Already a channel ID
    if channel_input.startswith("UC"):
        return channel_input

    # 2. Extract from URL
    parsed = urlparse(channel_input)
    if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        path = parsed.path
        # /@handle
        if path.startswith("/@"):
            handle = path.split("/")[1].lstrip("@")
            return _resolve_handle_to_id(handle)
        # /channel/UC...
        if path.startswith("/channel/"):
            channel_id = path.split("/")[2]
            if channel_id.startswith("UC"):
                return channel_id

    # 3. Is it a handle?
    if channel_input.startswith("@"):
        handle = channel_input.lstrip("@")
        return _resolve_handle_to_id(handle)
    
    # 4. Try as a handle anyway if it doesn't look like a URL
    if "://" not in channel_input:
        handle = channel_input.lstrip("@")
        try:
            return _resolve_handle_to_id(handle)
        except ValueError:
            pass

    raise ValueError(f"Could not resolve channel input to a channel ID: {channel_input}")


def _resolve_handle_to_id(handle: str) -> str:
    """Helper to resolve a YouTube handle to a channel ID using the API."""
    youtube_api_url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "id",
        "forHandle": handle,
        "key": os.getenv("GOOGLE_APIS_KEY")
    }
    resp = requests.get(youtube_api_url, params=params)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    if items:
        return items[0]["id"]
    raise ValueError(f"Could not resolve handle @{handle} to a channel ID.")


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


def save_transcripts_from_json(json_path: str, output_dir: str = "output") -> None:
    """Read a JSON file of video IDs and save their transcripts to individual text files.
    Implements resume capability, randomized delays, and progress tracking.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    video_ids = data.get("video_ids", [])
    if not video_ids:
        print("No video IDs found in the JSON file.")
        return

    channel_id = data.get("channel_id", "unknown_channel")
    folder_path = os.path.join(output_dir, f"{channel_id}_transcripts")
    os.makedirs(folder_path, exist_ok=True)

    total = len(video_ids)
    success_count = 0
    fail_count = 0
    skipped_count = 0

    print(f"Starting pipeline for {total} videos...")
    
    for index, video_id in enumerate(video_ids, start=1):
        file_path = os.path.join(folder_path, f"{video_id}.txt")
        
        if os.path.exists(file_path):
            skipped_count += 1
            continue

        print(f"[{index}/{total}] Processing {video_id}...", end="\r")
        try:
            transcript = get_transcript(video_id)
            with open(file_path, "w", encoding="utf-8") as f_out:
                f_out.write(transcript)
            success_count += 1
        except RuntimeError as e:
            fail_count += 1
            print(f"\n[!] Error on {video_id}: {e}")
            if "blocked" in str(e).lower():
                print("Detected block. Pausing for 5 minutes to cooldown...")
                time.sleep(300)
            continue

        time.sleep(random.uniform(2, 5))

        if index % 10 == 0:
            print(f"\n--- Progress Update: {index}/{total} | Success: {success_count} | Failed: {fail_count} | Skipped: {skipped_count} ---")

    print(f"\n\nPipeline Complete!")
    print(f"Total: {total}")
    print(f"Successful: {success_count}")
    print(f"Failed: {fail_count}")
    print(f"Skipped: {skipped_count}")
    print(f"Transcripts saved to {folder_path}")


def summarize_transcripts_from_folder(transcripts_folder: str, output_dir: str = "output") -> None:
    """Summarize all transcripts in a folder using Gemini, without re-fetching from YouTube.
    Expects files to be named {video_id}.txt.
    """
    if not os.path.isdir(transcripts_folder):
        print(f"Error: {transcripts_folder} is not a valid directory.")
        return

    files = [f for f in os.listdir(transcripts_folder) if f.endswith(".txt")]
    if not files:
        print("No .txt files found in the folder.")
        return

    print(f"Found {len(files)} transcripts to summarize...")

    # Determine channel metadata from the first successful video to organize output
    first_video_id = files[0].replace(".txt", "")
    try:
        channel_id = extract_channel_id(first_video_id)
        channel_title = extract_channel_title(first_video_id) or "Unknown"
    except Exception:
        channel_id = "unknown"
        channel_title = "Unknown"

    folder_name = sanitize_folder_name(f"{channel_id}_{channel_title}")
    output_path = os.path.join(output_dir, folder_name)
    os.makedirs(output_path, exist_ok=True)

    success_count = 0
    fail_count = 0
    skipped_count = 0

    for index, filename in enumerate(files, start=1):
        video_id = filename.replace(".txt", "")
        
        # Check if summary already exists (Resume capability)
        summary_file = os.path.join(output_path, f"{video_id}.json")
        if os.path.exists(summary_file):
            skipped_count += 1
            continue

        print(f"[{index}/{len(files)}] Summarizing {video_id}...", end="\r")
        
        try:
            with open(os.path.join(transcripts_folder, filename), "r", encoding="utf-8") as f:
                text = f.read()
            
            if not text.strip():
                print(f"\nSkipping {video_id}: Transcript file is empty.")
                fail_count += 1
                continue

            summary = summarize_transcript(text)
            
            # Enrich with metadata
            summary["video_id"] = video_id
            summary["video_url"] = f"https://www.youtube.com/watch?v={video_id}"
            try:
                summary["video_title"] = extract_video_title(video_id)
                summary["channel_id"] = channel_id
                summary["channel_title"] = channel_title
            except Exception:
                pass

            with open(summary_file, "w", encoding="utf-8") as f_out:
                json.dump(summary, f_out, indent=4)
            
            success_count += 1
        except Exception as e:
            fail_count += 1
            print(f"\n[!] Error summarizing {video_id}: {e}")
            # Gemini might rate limit us
            if "429" in str(e) or "quota" in str(e).lower():
                print("Gemini quota reached. Sleeping for 60 seconds...")
                time.sleep(60)
            continue

        if index % 10 == 0:
            print(f"\n--- Progress Update: {index}/{len(files)} | Success: {success_count} | Failed: {fail_count} | Skipped: {skipped_count} ---")

    print(f"\n\nLocal Summarization Complete!")
    print(f"Total: {len(files)}")
    print(f"Successful: {success_count}")
    print(f"Failed: {fail_count}")
    print(f"Summaries saved to {output_path}")


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
