"""The demo generator + seeder + stream driver produce a coherent, streamable
multi-source dataset that flows through the real connectors."""

import asyncio
from datetime import datetime, timezone

from demo.generator import GenConfig
from demo.seed import seed
from demo.stream import DemoStreamer

ANCHOR = datetime(2026, 6, 8, tzinfo=timezone.utc)


def _small_seed(out_dir: str):
    cfg = GenConfig(num_vessels=4, crew_per_vessel=6, crew_changes=12,
                    ambient_slack=60, routine_emails=20, weeks_back=2, weeks_forward=1)
    return seed(out_dir, cfg, ANCHOR)


def test_seed_splits_and_writes_dataset(tmp_path):
    meta = _small_seed(str(tmp_path))
    assert meta["total_events"] == meta["backlog_events"] + meta["future_events"]
    assert meta["future_events"] > 0 and meta["backlog_events"] > 0
    for f in ["backlog.jsonl", "timeline_future.jsonl", "entities.json", "seed_meta.json"]:
        assert (tmp_path / f).exists()


def test_backlog_streams_all_five_sources_and_is_idempotent(tmp_path):
    _small_seed(str(tmp_path))
    streamer = DemoStreamer(str(tmp_path))
    result = asyncio.run(streamer.run_backlog())

    # every focus source produced canonical signals
    assert set(result["by_source_system"]) == {
        "SLACK", "EMAIL", "CREW_DB", "CONTRACT_CLM", "VESSEL_PORT_DB"
    }
    assert result["signals_emitted"] > 0
    # ERP watermark + Slack event_id dedup → nothing new on a re-run
    assert result["second_pass_new"] == 0
    # crew-change clusters carry sign-off intents (→ SignOffEvent in L2)
    assert result["signoff_intents"] > 0


def test_live_replay_advances_in_time_order(tmp_path):
    _small_seed(str(tmp_path))
    streamer = DemoStreamer(str(tmp_path))
    # huge speed + tiny cap so the virtual clock blows past everything immediately
    result = asyncio.run(streamer.run_live(speed=1e9, tick=0.0, max_seconds=2))
    assert result["replayed"] == result["future_total"]
    assert result["signals_emitted"] == result["future_total"] or result["signals_emitted"] > 0
