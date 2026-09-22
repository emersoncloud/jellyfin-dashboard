from prometheus_client import CollectorRegistry, generate_latest

import copy

from exporter import JellyfinCollector, describe_track, format_title, parse_session, reconcile

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
            {"Type": "Audio", "Index": 1, "Codec": "truehd", "BitRate": 4_000_000, "Channels": 8},
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
        "AudioChannels": 2,
        "IsVideoDirect": False,
        "IsAudioDirect": False,
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
            {"Type": "Audio", "Index": 2, "Codec": "ac3", "BitRate": 640_000, "Channels": 6},
        ],
    },
    "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": True, "AudioStreamIndex": 2},
}

# Shape captured from Jellyfin 12.1: HEVC copied, DTS audio transcoded for a browser.
AUDIO_ONLY_TRANSCODE_SESSION = {
    "Id": "5555666677778888",
    "UserName": "root",
    "DeviceName": "Chrome",
    "Client": "Jellyfin Web",
    "NowPlayingItem": {
        "Type": "Movie",
        "Name": "Arrival",
        "ProductionYear": 2016,
        "MediaStreams": [
            {"Type": "Video", "Index": 0, "Codec": "hevc", "Width": 1920, "Height": 1080},
            {"Type": "Audio", "Index": 1, "Codec": "dts", "Channels": 6},
        ],
    },
    "PlayState": {"PlayMethod": "Transcode", "AudioStreamIndex": 1},
    "TranscodingInfo": {
        "AudioCodec": "aac",
        "VideoCodec": "hevc",
        "IsVideoDirect": True,
        "IsAudioDirect": False,
        "AudioChannels": 2,
        "Bitrate": 14_409_495,
        "HardwareAccelerationType": "qsv",
        "TranscodeReasons": ["AudioCodecNotSupported"],
    },
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
    assert s.video == "Transcode · hevc → h264"
    assert s.audio == "Transcode · truehd 7.1 → aac stereo"
    assert s.hw_accel == "qsv"
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
    assert s.video == "Direct · h264"
    assert s.audio == "Direct · ac3 5.1"  # selected track, not the first


def test_audio_only_transcode():
    s = parse_session(AUDIO_ONLY_TRANSCODE_SESSION)
    assert s.video == "Direct · hevc"
    assert s.audio == "Transcode · dts 5.1 → aac stereo"


def test_direct_flags_inferred_when_missing():
    session = {**AUDIO_ONLY_TRANSCODE_SESSION, "TranscodingInfo": {"VideoCodec": "hevc", "AudioCodec": "aac"}}
    s = parse_session(session)
    assert s.video == "Direct · hevc"
    assert s.audio.startswith("Transcode · dts")


def test_describe_track_formats():
    assert describe_track(True, "H264", None) == "Direct · h264"
    assert describe_track(False, "eac3", "aac", 6, None) == "Transcode · eac3 5.1 → aac 5.1"
    assert describe_track(False, "flac", "aac", 4, 2) == "Transcode · flac 4ch → aac stereo"


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


def _variant(base, *, play_method=None, paused=None, tinfo=True):
    """Same playback as `base`, as Jellyfin reports it at a later moment."""
    s = copy.deepcopy(base)
    if play_method is not None:
        s["PlayState"]["PlayMethod"] = play_method
    if paused is not None:
        s["PlayState"]["IsPaused"] = paused
    if not tinfo:
        s.pop("TranscodingInfo", None)
    return s


def test_transcode_without_info_is_unknown_not_direct():
    s = parse_session(_variant(TRANSCODE_SESSION, tinfo=False))
    assert s.method == "Transcode"
    assert (s.video, s.audio) == ("Unknown", "Unknown")


def test_reconcile_keeps_details_while_throttled_and_paused():
    """Replays the sequence seen live: transcoding → throttled → paused long enough that Jellyfin kills the job."""
    collector = JellyfinCollector("http://x", "key")
    timeline = [
        _variant(TRANSCODE_SESSION),                                                  # full info
        _variant(TRANSCODE_SESSION, tinfo=False),                                     # throttled
        _variant(TRANSCODE_SESSION, play_method="DirectPlay", paused=True, tinfo=False),  # job killed while paused
    ]
    streams = []
    for session in timeline:
        streams = [reconcile(s, collector._last.get(s.key)) for s in [parse_session(session)]]
        collector._last = {s.key: s for s in streams}
        s = streams[0]
        assert s.method == "Transcode"
        assert s.video == "Transcode · hevc → h264"
        assert s.audio == "Transcode · truehd 7.1 → aac stereo"
        assert s.hw_accel == "qsv"


def test_reconcile_trusts_genuine_switch_to_direct_play():
    last = parse_session(TRANSCODE_SESSION)
    now = parse_session(_variant(TRANSCODE_SESSION, play_method="DirectPlay", paused=False, tinfo=False))
    s = reconcile(now, last)
    assert s.method == "DirectPlay"
    assert s.video.startswith("Direct")


def test_collector_remembers_across_scrapes_and_forgets_ended():
    collector = JellyfinCollector("http://x", "key")
    feed = iter([[TRANSCODE_SESSION], [_variant(TRANSCODE_SESSION, play_method="DirectPlay", paused=True, tinfo=False)], []])
    collector.fetch_sessions = lambda: next(feed)
    registry = CollectorRegistry()
    registry.register(collector)
    generate_latest(registry)
    out = generate_latest(registry).decode()
    assert 'method="Transcode"' in out and 'video="Transcode · hevc → h264"' in out
    generate_latest(registry)
    assert collector._last == {}
