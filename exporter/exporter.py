"""Prometheus exporter for live Jellyfin playback sessions.

Polls /Sessions on every scrape (no background loop, so data is never stale).
"""

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass

from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY, GaugeMetricFamily

log = logging.getLogger("jellyfin-exporter")

STREAM_LABELS = ["session", "user", "device", "client", "title", "media_type", "method"]
INFO_LABELS = STREAM_LABELS + ["video_codec", "audio_codec", "hw_accel", "transcode_reasons", "resolution"]


@dataclass(frozen=True)
class Stream:
    session: str
    user: str
    device: str
    client: str
    title: str
    media_type: str
    method: str  # DirectPlay | DirectStream | Transcode
    paused: bool
    bitrate: int  # bits/sec actually being sent
    progress: float  # 0..1
    video_codec: str
    audio_codec: str
    hw_accel: str
    transcode_reasons: str
    resolution: str
    transcode_fps: float | None

    @property
    def labels(self) -> list[str]:
        return [self.session, self.user, self.device, self.client, self.title, self.media_type, self.method]

    @property
    def info_labels(self) -> list[str]:
        return self.labels + [self.video_codec, self.audio_codec, self.hw_accel, self.transcode_reasons, self.resolution]


def format_title(item: dict) -> str:
    if item.get("Type") == "Episode":
        s, e = item.get("ParentIndexNumber"), item.get("IndexNumber")
        code = f"S{s:02d}E{e:02d}" if s is not None and e is not None else ""
        return " - ".join(p for p in (item.get("SeriesName"), code, item.get("Name")) if p)
    year = item.get("ProductionYear")
    return f"{item.get('Name', '?')} ({year})" if year else item.get("Name", "?")


def _selected_streams(item: dict, play_state: dict) -> tuple[dict, dict]:
    streams = item.get("MediaStreams") or []
    video = next((s for s in streams if s.get("Type") == "Video"), {})
    audio_idx = play_state.get("AudioStreamIndex")
    audio = next((s for s in streams if s.get("Type") == "Audio" and s.get("Index") == audio_idx), None)
    audio = audio or next((s for s in streams if s.get("Type") == "Audio"), {})
    return video, audio


def parse_session(session: dict) -> Stream | None:
    item = session.get("NowPlayingItem")
    if not item:
        return None

    play_state = session.get("PlayState") or {}
    tinfo = session.get("TranscodingInfo") or {}
    video, audio = _selected_streams(item, play_state)

    method = play_state.get("PlayMethod") or ("Transcode" if tinfo else "DirectPlay")
    if tinfo.get("Bitrate"):
        bitrate = int(tinfo["Bitrate"])
    else:
        bitrate = int(video.get("BitRate") or 0) + int(audio.get("BitRate") or 0)

    ticks, runtime = play_state.get("PositionTicks") or 0, item.get("RunTimeTicks") or 0
    width = tinfo.get("Width") or video.get("Width")
    height = tinfo.get("Height") or video.get("Height")

    return Stream(
        session=session.get("Id", "")[:8],
        user=session.get("UserName") or "unknown",
        device=session.get("DeviceName") or "",
        client=session.get("Client") or "",
        title=format_title(item),
        media_type=item.get("Type") or "",
        method=method,
        paused=bool(play_state.get("IsPaused")),
        bitrate=bitrate,
        progress=ticks / runtime if runtime else 0.0,
        video_codec=(tinfo.get("VideoCodec") or video.get("Codec") or "").lower(),
        audio_codec=(tinfo.get("AudioCodec") or audio.get("Codec") or "").lower(),
        hw_accel=tinfo.get("HardwareAccelerationType") or ("none" if tinfo else ""),
        transcode_reasons=",".join(tinfo.get("TranscodeReasons") or []),
        resolution=f"{width}x{height}" if width and height else "",
        transcode_fps=tinfo.get("Framerate"),
    )


class JellyfinCollector:
    def __init__(self, url: str, api_key: str, timeout: float = 5.0):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f'MediaBrowser Token="{api_key}"', "Accept": "application/json"}
        self.timeout = timeout

    def describe(self):
        # Skip auto-describe, which would hit Jellyfin at registration time.
        return []

    def fetch_sessions(self) -> list[dict]:
        req = urllib.request.Request(f"{self.url}/Sessions?activeWithinSeconds=120", headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)

    def collect(self):
        up = GaugeMetricFamily("jellyfin_up", "1 if the Jellyfin API responded")
        started = time.monotonic()
        try:
            sessions = self.fetch_sessions()
        except Exception as e:
            log.warning("Jellyfin scrape failed: %s", e)
            up.add_metric([], 0)
            yield up
            return
        up.add_metric([], 1)
        yield up

        duration = GaugeMetricFamily("jellyfin_scrape_duration_seconds", "Time spent querying Jellyfin")
        duration.add_metric([], time.monotonic() - started)
        yield duration

        streams = [s for s in map(parse_session, sessions) if s]

        info = GaugeMetricFamily("jellyfin_stream_info", "Active stream (always 1)", labels=INFO_LABELS)
        bitrate = GaugeMetricFamily("jellyfin_stream_bitrate_bps", "Bits/sec being sent to the client", labels=STREAM_LABELS)
        paused = GaugeMetricFamily("jellyfin_stream_paused", "1 if paused", labels=STREAM_LABELS)
        progress = GaugeMetricFamily("jellyfin_stream_progress_ratio", "Playback position 0..1", labels=STREAM_LABELS)
        fps = GaugeMetricFamily("jellyfin_transcode_fps", "Transcoder output framerate", labels=STREAM_LABELS)
        for s in streams:
            info.add_metric(s.info_labels, 1)
            bitrate.add_metric(s.labels, 0 if s.paused else s.bitrate)
            paused.add_metric(s.labels, int(s.paused))
            progress.add_metric(s.labels, s.progress)
            if s.transcode_fps is not None:
                fps.add_metric(s.labels, s.transcode_fps)

        active = GaugeMetricFamily("jellyfin_active_streams", "Active streams by play method", labels=["method"])
        for method in ("DirectPlay", "DirectStream", "Transcode"):
            active.add_metric([method], sum(1 for s in streams if s.method == method))

        yield from (info, bitrate, paused, progress, fps, active)


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    url, api_key = os.environ["JELLYFIN_URL"], os.environ["JELLYFIN_API_KEY"]
    port = int(os.environ.get("EXPORTER_PORT", "9711"))

    REGISTRY.register(JellyfinCollector(url, api_key))
    start_http_server(port)
    log.info("Serving metrics on :%d for %s", port, url)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
