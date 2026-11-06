
import dotenv
import argparse
import json
import os

from utils import extract_video_id
from utils import extract_video_title
from utils import get_transcript
from utils import summarize_transcript
from utils import save_summary_to_json
from utils import save_all_channel_video_ids_to_json
from utils import summarize_channel_videos
from utils import resolve_channel_id
from utils import save_transcripts_from_json
from utils import summarize_transcripts_from_folder

dotenv.load_dotenv()

parser = argparse.ArgumentParser()
parser.add_argument("--video_transcript", help="YouTube video URL to fetch the transcript for")
parser.add_argument("--ai_video_summary", help="YouTube video URL to fetch the transcript and summarize it using Gemini")
parser.add_argument("--ai_video_summary_out", help="File to save the AI-generated video summary")
parser.add_argument("--all_channel_videos", help="Fetch all video IDs from a channel. Provide a Channel ID (UC...), a handle (@handle), or a channel URL.")
parser.add_argument("--channel_summaries", help="Fetch and summarize every video uploaded by a channel. Provide a Channel ID (UC...), a handle (@handle), or a channel URL.")
parser.add_argument("--channel_transcripts", help="Save transcripts for all videos listed in a channel IDs JSON file (e.g., output/UC..._video_ids.json)")
parser.add_argument("--summarize_local_transcripts", help="Summarize transcripts from a local folder (e.g., output/UC..._transcripts)")
args = parser.parse_args()


if args.video_transcript:
    VIDEO_ID = extract_video_id(args.video_transcript)
    if VIDEO_ID:
        transcript_text = get_transcript(VIDEO_ID)

if args.ai_video_summary:
    VIDEO_ID = extract_video_id(args.ai_video_summary)
    if VIDEO_ID:
        transcript_text = get_transcript(VIDEO_ID)
        result = summarize_transcript(transcript_text)

if args.ai_video_summary_out:
    save_summary_to_json(args.ai_video_summary_out)

if args.all_channel_videos:
    CHANNEL_ID = resolve_channel_id(args.all_channel_videos)
    save_all_channel_video_ids_to_json(CHANNEL_ID)

if args.channel_summaries:
    CHANNEL_ID = resolve_channel_id(args.channel_summaries)
    summarize_channel_videos(CHANNEL_ID)

if args.channel_transcripts:
    save_transcripts_from_json(args.channel_transcripts)

if args.summarize_local_transcripts:
    summarize_transcripts_from_folder(args.summarize_local_transcripts)