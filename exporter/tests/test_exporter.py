from prometheus_client import CollectorRegistry, generate_latest

from exporter import JellyfinCollector, format_title, parse_session

TRANSCODE_SESSION = {
    "Id": "abcdef1234567890",
    "UserName": "alice",
    "DeviceName": "Living Room TV",
    "Client": "Jellyfin Android TV",
    "NowPlayingItem": {
        "Type": "Episode",
        "Name": "Pilot",
        "SeriesName": "Severance",
        "ParentIndexNumber": 1,
        "IndexNumber": 1,
        "RunTimeTicks": 1000,
        "MediaStreams": [
            {"Type": "Video", "Index": 0, "Codec": "hevc", "BitRate": 20_000_000, "Width": 3840, "Height": 2160},
            {"Type": "Audio", "Index": 1, "Codec": "truehd", "BitRate": 4_000_000},
        ],
    },
    "PlayState": {"PlayMethod": "Transcode", "IsPaused": False, "PositionTicks": 250, "AudioStreamIndex": 1},
    "TranscodingInfo": {
        "Bitrate": 8_000_000,
        "VideoCodec": "h264",
        "AudioCodec": "aac",
        "Width": 1920,
        "Height": 1080,
        "Framerate": 48.5,
        "HardwareAccelerationType": "qsv",
        "TranscodeReasons": ["VideoCodecNotSupported", "AudioCodecNotSupported"],
    },
}

DIRECT_SESSION = {
    "Id": "1111222233334444",
    "UserName": "bob",
    "DeviceName": "iPhone",
    "Client": "Infuse",
    "NowPlayingItem": {
        "Type": "Movie",
        "Name": "Dune",
        "ProductionYear": 2021,
        "RunTimeTicks": 0,
        "MediaStreams": [
            {"Type": "Video", "Index": 0, "Codec": "h264", "BitRate": 10_000_000},
            {"Type": "Audio", "Index": 1, "Codec": "aac", "BitRate": 256_000},
            {"Type": "Audio", "Index": 2, "Codec": "ac3", "BitRate": 640_000},
        ],
    },
    "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": True, "AudioStreamIndex": 2},
}

IDLE_SESSION = {"Id": "idle", "UserName": "carol", "PlayState": {}}


def test_format_title():
    assert format_title(TRANSCODE_SESSION["NowPlayingItem"]) == "Severance - S01E01 - Pilot"
    assert format_title(DIRECT_SESSION["NowPlayingItem"]) == "Dune (2021)"
    assert format_title({"Type": "Episode", "Name": "Special", "SeriesName": "X"}) == "X - Special"


def test_transcode_uses_output_bitrate_and_info():
    s = parse_session(TRANSCODE_SESSION)
    assert s.method == "Transcode"
    assert s.bitrate == 8_000_000
    assert s.progress == 0.25
    assert s.resolution == "1920x1080"
    assert (s.video_codec, s.audio_codec, s.hw_accel) == ("h264", "aac", "qsv")
    assert s.transcode_reasons == "VideoCodecNotSupported,AudioCodecNotSupported"
    assert s.transcode_fps == 48.5
    assert s.session == "abcdef12"


def test_direct_play_sums_selected_streams():
    s = parse_session(DIRECT_SESSION)
    assert s.method == "DirectPlay"
    assert s.bitrate == 10_640_000  # video + selected (index 2) audio
    assert s.paused
    assert s.progress == 0.0
    assert s.hw_accel == ""


def test_idle_session_ignored():
    assert parse_session(IDLE_SESSION) is None


def _scrape(sessions=None, error=None) -> str:
    collector = JellyfinCollector("http://x", "key")

    def fetch():
        if error:
            raise error
        return sessions

    collector.fetch_sessions = fetch
    registry = CollectorRegistry()
    registry.register(collector)
    return generate_latest(registry).decode()


def test_collect_metrics():
    out = _scrape([TRANSCODE_SESSION, DIRECT_SESSION, IDLE_SESSION])
    assert "jellyfin_up 1.0" in out
    assert 'jellyfin_active_streams{method="Transcode"} 1.0' in out
    assert 'jellyfin_active_streams{method="DirectPlay"} 1.0' in out
    assert 'user="alice"' in out and 'user="bob"' in out and 'user="carol"' not in out
    # paused stream reports 0 bandwidth
    bob_bitrate = next(l for l in out.splitlines() if l.startswith("jellyfin_stream_bitrate_bps") and 'user="bob"' in l)
    assert bob_bitrate.endswith(" 0.0")


def test_collect_reports_down_on_error():
    out = _scrape(error=OSError("boom"))
    assert "jellyfin_up 0.0" in out
    assert "jellyfin_stream_info" not in out
