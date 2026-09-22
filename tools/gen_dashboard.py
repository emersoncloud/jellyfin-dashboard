import json
import sys

DS = {"type": "prometheus", "uid": "prometheus"}
U = 'user=~"$user"'
CPU = '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])))'
NIC = 'max(rate(node_network_transmit_bytes_total{device=~"eth.*|en.*|bond.*|br0"}[$__rate_interval])) * 8'

_id = 0


def pid():
    global _id
    _id += 1
    return _id


def target(expr, legend="", ref="A", instant=False, fmt="time_series"):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref, "format": fmt}
    if instant:
        t |= {"instant": True, "range": False}
    return t


def stat(title, expr, x, unit="none", thresholds=None, mappings=None, decimals=None):
    steps = [{"color": "green", "value": None}] + [{"color": c, "value": v} for v, c in (thresholds or [])]
    defaults = {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}, "mappings": mappings or []}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "id": pid(), "type": "stat", "title": title, "datasource": DS,
        "gridPos": {"x": x, "y": 0, "w": 4, "h": 4},
        "targets": [target(expr)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"colorMode": "background", "graphMode": "area", "reduceOptions": {"calcs": ["lastNotNull"]}, "textMode": "value"},
    }


def ts(title, targets, grid, unit="none", stack=False, overrides=None, draw="line", fill=15, desc="", interp="smooth", minv=None, softmax=None):
    return {
        "id": pid(), "type": "timeseries", "title": title, "description": desc, "datasource": DS, "gridPos": grid,
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                **({"min": minv} if minv is not None else {}),
                **({"softMax": softmax, "decimals": 0} if softmax is not None else {}),
                "custom": {
                    "drawStyle": draw, "lineInterpolation": interp, "lineWidth": 1, "fillOpacity": fill, "spanNulls": 30000,
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                    "showPoints": "never",
                },
            },
            "overrides": overrides or [],
        },
        "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}},
    }


def override(name, props):
    return {"matcher": {"id": "byName", "options": name}, "properties": [{"id": k, "value": v} for k, v in props.items()]}


TRACK_MODE_COLORS = [
    {"type": "regex", "options": {"pattern": "^Transcode.*", "result": {"color": "orange", "index": 0}}},
    {"type": "regex", "options": {"pattern": "^Direct.*", "result": {"color": "green", "index": 1}}},
    # Jellyfin sent no transcode details and none were seen earlier for this playback.
    {"type": "value", "options": {"Unknown": {"color": "gray", "text": "Transcode · details unavailable", "index": 2}}},
]


def cpu_series(name="CPU"):
    return override(name, {
        "unit": "percent", "min": 0, "max": 100, "custom.axisPlacement": "right", "custom.stacking": {"mode": "none"},
        "custom.fillOpacity": 0, "custom.lineWidth": 2, "color": {"mode": "fixed", "fixedColor": "red"},
    })


panels = [
    stat("Streams", "sum(jellyfin_active_streams) or vector(0)", 0),
    stat("Transcoding", 'sum(jellyfin_active_streams{method="Transcode"}) or vector(0)', 4, thresholds=[(1, "orange"), (3, "red")]),
    stat("Stream bandwidth", "sum(jellyfin_stream_bitrate_bps) or vector(0)", 8, unit="bps"),
    stat("NIC upload", NIC, 12, unit="bps"),
    stat("CPU", CPU, 16, unit="percent", thresholds=[(70, "orange"), (90, "red")], decimals=0),
    stat("Jellyfin", "max(jellyfin_up) or vector(0)", 20, thresholds=[], mappings=[
        {"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}]),

    ts("Who's streaming vs CPU", [
        target(f"sum by (user) (jellyfin_stream_bitrate_bps{{{U}}})", "{{user}}", "A"),
        target(CPU, "CPU", "B"),
    ], {"x": 0, "y": 4, "w": 24, "h": 11}, unit="bps", stack=True, minv=0, overrides=[cpu_series()],
       desc="Stacked bitrate per user (left axis) with host CPU % (right axis, red)."),

    {
        "id": pid(), "type": "table", "title": "Now playing", "datasource": DS,
        "gridPos": {"x": 0, "y": 15, "w": 24, "h": 8},
        "targets": [
            target(f"max by (session, user, device, client, title, method, video, audio, resolution, hw_accel, transcode_reasons) (jellyfin_stream_info{{{U}}})", ref="A", instant=True, fmt="table"),
            target("max by (session) (jellyfin_stream_bitrate_bps)", ref="B", instant=True, fmt="table"),
            target("max by (session) (jellyfin_stream_progress_ratio) * 100", ref="C", instant=True, fmt="table"),
            target("max by (session) (jellyfin_stream_paused)", ref="D", instant=True, fmt="table"),
        ],
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "Value #A": True, "session": True},
                "indexByName": {"user": 0, "title": 1, "Value #D": 2, "method": 3, "video": 4, "audio": 5,
                                "Value #B": 6, "resolution": 7, "hw_accel": 8, "transcode_reasons": 9,
                                "Value #C": 10, "device": 11, "client": 12},
                "renameByName": {"user": "User", "title": "Title", "Value #D": "State", "method": "Method", "Value #B": "Bitrate",
                                 "resolution": "Resolution", "video": "Video", "audio": "Audio", "hw_accel": "HW accel",
                                 "transcode_reasons": "Transcode reasons", "Value #C": "Progress", "device": "Device", "client": "Client"},
            }},
        ],
        "fieldConfig": {
            "defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
            "overrides": [
                override("Bitrate", {"unit": "bps"}),
                override("Video", {"mappings": TRACK_MODE_COLORS, "custom.cellOptions": {"type": "color-text"}, "custom.width": 210}),
                override("Audio", {"mappings": TRACK_MODE_COLORS, "custom.cellOptions": {"type": "color-text"}, "custom.width": 300}),
                override("Title", {"custom.width": 300}),
                override("User", {"custom.width": 120}),
                *[override(col, {"custom.width": w}) for col, w in (("State", 110), ("Method", 110), ("Bitrate", 100), ("HW accel", 90))],
                override("Progress", {"unit": "percent", "min": 0, "max": 100, "custom.cellOptions": {"type": "gauge", "mode": "basic"}, "custom.width": 160}),
                override("State", {"mappings": [{"type": "value", "options": {"0": {"text": "▶ Playing", "color": "green"}, "1": {"text": "⏸ Paused", "color": "yellow"}}}],
                                   "custom.cellOptions": {"type": "color-text"}}),
                override("Method", {"mappings": [{"type": "value", "options": {
                    "Transcode": {"color": "orange"}, "DirectStream": {"color": "blue"}, "DirectPlay": {"color": "green"}}}],
                                    "custom.cellOptions": {"type": "color-text"}}),
            ],
        },
        "options": {"showHeader": True, "sortBy": [{"displayName": "User", "desc": False}]},
    },

    ts("Streams by play method", [
        target("sum by (method) (jellyfin_active_streams)", "{{method}}"),
    ], {"x": 0, "y": 23, "w": 12, "h": 8}, stack=True, interp="stepAfter", fill=60, minv=0, softmax=4, overrides=[
        override("Transcode", {"color": {"mode": "fixed", "fixedColor": "orange"}}),
        override("DirectStream", {"color": {"mode": "fixed", "fixedColor": "blue"}}),
        override("DirectPlay", {"color": {"mode": "fixed", "fixedColor": "green"}}),
    ]),
    ts("Transcoder FPS", [
        target(f"jellyfin_transcode_fps{{{U}}}", "{{user}} · {{title}}"),
    ], {"x": 12, "y": 23, "w": 12, "h": 8}, unit="fps", fill=0,
       desc="Output framerate of each transcode. Below the source framerate (~24) means the server can't keep up → buffering."),

    ts("CPU by mode", [
        target('sum by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[$__rate_interval])) / scalar(count(count by (cpu) (node_cpu_seconds_total))) * 100', "{{mode}}"),
    ], {"x": 0, "y": 31, "w": 12, "h": 8}, unit="percent", stack=True),
    ts("Network: NIC upload vs Jellyfin streams", [
        target(NIC, "NIC upload"),
        target("sum(jellyfin_stream_bitrate_bps)", "Jellyfin streams", "B"),
    ], {"x": 12, "y": 31, "w": 12, "h": 8}, unit="bps", fill=10,
       desc="Gap between the two lines = non-Jellyfin upload (downloads seeding, backups, etc.)."),
]

dashboard = {
    "uid": "jellyfin-live",
    "title": "Jellyfin Live",
    "tags": ["jellyfin"],
    "timezone": "browser",
    "refresh": "10s",
    "time": {"from": "now-6h", "to": "now"},
    "schemaVersion": 39,
    "editable": True,
    "templating": {"list": [{
        "name": "user", "label": "User", "type": "query", "datasource": DS,
        "query": {"query": "label_values(jellyfin_stream_info, user)", "refId": "user"},
        "definition": "label_values(jellyfin_stream_info, user)",
        "refresh": 2, "multi": True, "includeAll": True, "allValue": ".*",
        "current": {"text": "All", "value": "$__all"},
    }]},
    "panels": panels,
}

for p in panels:
    refs = [t["refId"] for t in p["targets"]]
    assert len(refs) == len(set(refs)), f"duplicate refId in {p['title']}"

json.dump(dashboard, open(sys.argv[1], "w"), indent=2)
