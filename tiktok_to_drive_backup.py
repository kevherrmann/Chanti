#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import mimetypes
import os
import pathlib
import re
import shutil
import subprocess
from typing import Any

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CHANNEL_URL = "https://www.tiktok.com/@crazyfamilylp"
DRIVE_FOLDER_NAME = "CrazyFamilyLP TikTok Backups"
BASE_DIR = pathlib.Path("/opt/data/tiktok_drive_backup")
DOWNLOAD_DIR = BASE_DIR / "downloads"
STATE_PATH = BASE_DIR / "state.json"
TOKEN_PATH = pathlib.Path("/opt/data/google_token.json")
YOUTUBE_TOKEN_PATH = pathlib.Path("/opt/data/youtube_token.json")
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
DEFAULT_LOOKBACK_DAYS = 4
DEFAULT_MAX_SCAN = 12
DEFAULT_YOUTUBE_PRIVACY = "private"


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"uploaded": {}}


def save_state(state: dict[str, Any]) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True))
    tmp.replace(STATE_PATH)


def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def get_recent_entries(max_scan: int) -> list[dict[str, Any]]:
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp is not installed")
    cmd = ["yt-dlp", "--dump-json", "--playlist-end", str(max_scan), CHANNEL_URL]
    p = run(cmd, timeout=180)
    if p.returncode != 0:
        raise RuntimeError(f"yt-dlp metadata failed: {p.stderr.strip() or p.stdout.strip()}")
    entries = []
    for line in p.stdout.splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


def parse_video_time(entry: dict[str, Any]) -> dt.datetime | None:
    ts = entry.get("timestamp") or entry.get("release_timestamp")
    if ts:
        return dt.datetime.fromtimestamp(int(ts), tz=dt.timezone.utc)
    upload_date = entry.get("upload_date")
    if upload_date and len(upload_date) == 8:
        return dt.datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=dt.timezone.utc)
    return None


def sanitize(s: str, max_len: int = 90) -> str:
    keep = []
    for ch in s:
        if ch.isalnum() or ch in " ._-()[]#@":
            keep.append(ch)
        else:
            keep.append("_")
    out = "".join(keep).strip().strip(".")
    return (out[:max_len].strip() or "tiktok_video")


def youtube_title_from_tiktok(title: str | None, fallback: str = "video", max_len: int = 100) -> str:
    """Create a YouTube title from the TikTok title, removing TikTok hashtags."""
    title = title or ""
    # Remove hashtag tokens including punctuation-only leftovers after them.
    without_hashtags = re.sub(r"(?<!\S)#[\w\u00C0-\uFFFF.-]+", "", title, flags=re.UNICODE)
    without_hashtags = re.sub(r"\s+", " ", without_hashtags).strip(" .,-–—|\t\n\r")
    if not without_hashtags:
        without_hashtags = f"TikTok {fallback}"
    return without_hashtags[:max_len].strip() or f"TikTok {fallback}"


THANK_YOU_TITLE_RE = re.compile(r"(?i)(?:\bvielen\s+dank\b|\bdanke\b|\bdankeschön\b)")


def should_skip_entry(entry: dict[str, Any]) -> bool:
    """Return True for thank-you/support videos that should not be archived or uploaded."""
    title = entry.get("title") or ""
    return bool(THANK_YOU_TITLE_RE.search(title))


def needs_processing(entry: dict[str, Any], state: dict[str, Any], youtube_upload: bool) -> bool:
    vid = str(entry.get("id") or "")
    if not vid or should_skip_entry(entry):
        return False
    uploaded_info = state.get("uploaded", {}).get(vid)
    if not uploaded_info:
        return True
    if youtube_upload and not uploaded_info.get("youtube_video_id"):
        return True
    return False


def find_downloaded_video(vid: str) -> pathlib.Path | None:
    candidates = sorted(DOWNLOAD_DIR.glob(f"{vid} - *"), key=lambda x: x.stat().st_mtime, reverse=True)
    video_files = [p for p in candidates if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}]
    return video_files[0] if video_files else None


def download_video(entry: dict[str, Any]) -> pathlib.Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    vid = str(entry["id"])
    title = sanitize(entry.get("title") or vid)
    outtmpl = str(DOWNLOAD_DIR / f"{vid} - {title}.%(ext)s")
    url = entry.get("webpage_url") or f"{CHANNEL_URL}/video/{vid}"
    # TikTok usually exposes a few pre-encoded variants. Prefer the highest bitrate/resolution
    # non-watermarked MP4 that yt-dlp can see. If TikTok exposes something better later,
    # this selector will automatically pick it.
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "best[ext=mp4]/best",
        "-S", "res,br,codec:avc:m4a",
        "--merge-output-format", "mp4",
        "-o", outtmpl,
        url,
    ]
    p = run(cmd, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f"yt-dlp download failed for {vid}: {p.stderr.strip() or p.stdout.strip()}")
    video_file = find_downloaded_video(vid)
    if not video_file:
        raise RuntimeError(f"Downloaded file not found for {vid}")
    return video_file


def credentials_from_file(token_path: pathlib.Path, scopes: list[str], service_name: str) -> Credentials:
    if not token_path.exists():
        raise RuntimeError(f"{service_name} token is missing: {token_path}")
    creds = Credentials.from_authorized_user_file(str(token_path), scopes=scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    if not creds.valid:
        raise RuntimeError(f"{service_name} token is invalid")
    return creds


def drive_service():
    creds = credentials_from_file(TOKEN_PATH, DRIVE_SCOPES, "Google Drive")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def youtube_service():
    creds = credentials_from_file(YOUTUBE_TOKEN_PATH, YOUTUBE_SCOPES, "YouTube")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def ensure_folder(service) -> str:
    q = "mimeType='application/vnd.google-apps.folder' and name=%r and trashed=false" % DRIVE_FOLDER_NAME.replace("'", "\\'")
    res = service.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=10).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": DRIVE_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}
    folder = service.files().create(body=meta, fields="id,name").execute()
    return folder["id"]


def upload_file(service, folder_id: str, path: pathlib.Path, entry: dict[str, Any]) -> dict[str, Any]:
    vid = str(entry["id"])
    media_type = mimetypes.guess_type(str(path))[0] or "video/mp4"
    desc = f"TikTok backup from {CHANNEL_URL}\nVideo ID: {vid}\nURL: {entry.get('webpage_url', '')}\nTitle: {entry.get('title', '')}"
    meta = {"name": path.name, "parents": [folder_id], "description": desc}
    media = MediaFileUpload(str(path), mimetype=media_type, resumable=True)
    return service.files().create(body=meta, media_body=media, fields="id,name,webViewLink,size").execute()


def upload_youtube_video(service, path: pathlib.Path, entry: dict[str, Any], privacy: str = DEFAULT_YOUTUBE_PRIVACY) -> dict[str, Any]:
    vid = str(entry["id"])
    title = youtube_title_from_tiktok(entry.get("title"), fallback=vid)
    source_url = entry.get("webpage_url") or f"{CHANNEL_URL}/video/{vid}"
    description = (
        f"#Shorts\n\n"
        f"Quelle: TikTok {CHANNEL_URL}\n"
        f"Original: {source_url}\n"
        f"TikTok Video ID: {vid}"
    )
    media_type = mimetypes.guess_type(str(path))[0] or "video/mp4"
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "20",  # Gaming
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(path), mimetype=media_type, chunksize=8 * 1024 * 1024, resumable=True)
    return service.videos().insert(part="snippet,status", body=body, media_body=media).execute()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    ap.add_argument("--max-scan", type=int, default=DEFAULT_MAX_SCAN)
    ap.add_argument("--only-video-id", help="Process only this TikTok video ID if it appears in the scan")
    ap.add_argument("--youtube-upload", dest="youtube_upload", action="store_true", default=True,
                    help="Upload processed videos to YouTube after Drive backup (default: enabled)")
    ap.add_argument("--no-youtube-upload", dest="youtube_upload", action="store_false",
                    help="Disable YouTube upload and only back up to Drive")
    ap.add_argument("--youtube-privacy", choices=["private", "unlisted", "public"], default=DEFAULT_YOUTUBE_PRIVACY)
    args = ap.parse_args()

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    uploaded = state.setdefault("uploaded", {})
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.lookback_days)

    log(f"Scanning {CHANNEL_URL}; lookback={args.lookback_days} days; max_scan={args.max_scan}; youtube_upload={args.youtube_upload}")
    entries = get_recent_entries(args.max_scan)
    log(f"Found {len(entries)} recent channel entries")

    candidates = []
    for e in entries:
        vid = str(e.get("id") or "")
        if not vid:
            continue
        if args.only_video_id and vid != args.only_video_id:
            continue
        if not needs_processing(e, state, youtube_upload=args.youtube_upload):
            continue
        t = parse_video_time(e)
        if t and t < cutoff:
            continue
        candidates.append(e)

    candidates.sort(key=lambda e: parse_video_time(e) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    if not candidates:
        log("No new videos to upload")
        state["last_run_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        save_state(state)
        return 0

    log(f"New videos to process: {len(candidates)}")
    if args.dry_run:
        for e in candidates:
            vid = str(e.get("id"))
            title = youtube_title_from_tiktok(e.get("title"), fallback=vid)
            log(f"DRY RUN: would process {vid} | YouTube title: {title!r} | {e.get('webpage_url')}")
        return 0

    drive = drive_service()
    folder_id = ensure_folder(drive)
    log(f"Using Drive folder '{DRIVE_FOLDER_NAME}' ({folder_id})")
    youtube = youtube_service() if args.youtube_upload else None

    failures = 0
    for e in candidates:
        vid = str(e["id"])
        try:
            info = uploaded.setdefault(vid, {})
            path = find_downloaded_video(vid)
            if path:
                log(f"Using existing download {path.name} ({path.stat().st_size} bytes)")
            else:
                log(f"Downloading {vid}: {e.get('title', '')[:80]}")
                path = download_video(e)

            if not info.get("drive_file_id"):
                log(f"Uploading to Drive: {path.name} ({path.stat().st_size} bytes)")
                result = upload_file(drive, folder_id, path, e)
                info.update({
                    "uploaded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "drive_file_id": result.get("id"),
                    "drive_link": result.get("webViewLink"),
                })
                log(f"Drive uploaded {vid}: {result.get('webViewLink') or result.get('id')}")
            else:
                log(f"Drive already uploaded for {vid}: {info.get('drive_link') or info.get('drive_file_id')}")

            if youtube and not info.get("youtube_video_id"):
                yt_title = youtube_title_from_tiktok(e.get("title"), fallback=vid)
                log(f"Uploading to YouTube ({args.youtube_privacy}): {yt_title}")
                yt_result = upload_youtube_video(youtube, path, e, privacy=args.youtube_privacy)
                info.update({
                    "youtube_uploaded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "youtube_video_id": yt_result.get("id"),
                    "youtube_privacy": args.youtube_privacy,
                    "youtube_title": yt_title,
                    "youtube_link": f"https://youtu.be/{yt_result.get('id')}" if yt_result.get("id") else None,
                })
                log(f"YouTube uploaded {vid}: {info.get('youtube_link') or yt_result.get('id')}")
            elif youtube:
                log(f"YouTube already uploaded for {vid}: {info.get('youtube_link') or info.get('youtube_video_id')}")

            info.update({
                "title": e.get("title"),
                "url": e.get("webpage_url"),
                "timestamp": e.get("timestamp"),
            })
            save_state(state)
        except Exception as ex:
            failures += 1
            log(f"ERROR processing {vid}: {ex}")

    state["last_run_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save_state(state)
    if failures:
        log(f"Completed with {failures} failure(s)")
        return 1
    log("Completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
