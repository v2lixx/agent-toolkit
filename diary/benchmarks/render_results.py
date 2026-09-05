"""Render inspectable, data-driven benchmark SVGs without plotting dependencies."""

from __future__ import annotations

import argparse
import html
import json
import math
import statistics
from pathlib import Path


INK = "#152e40"
MUTED = "#566b79"
GRID = "#dae3e7"
TEAL = "#087f78"
ORANGE = "#bd581d"


def text(x, y, value, size=18, color=INK, anchor="start", weight="400"):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{html.escape(str(value))}</text>')


def line(x1, y1, x2, y2, color=GRID, width=1):
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{color}" stroke-width="{width}"/>')


def document(title, description, content, width=1400, height=760):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'role="img" aria-labelledby="title desc">\n'
            f'<title id="title">{html.escape(title)}</title>\n'
            f'<desc id="desc">{html.escape(description)}</desc>\n'
            f'<rect width="{width}" height="{height}" rx="20" fill="#f6f9fa"/>\n'
            '<g font-family="Arial, Helvetica, sans-serif">\n' + "\n".join(content) +
            '\n</g>\n</svg>\n')


def render_mcp(data):
    groups = [g for g in data["groups"] if g["mode"] == "warm"]
    if len(groups) != 3:
        raise ValueError("Expected three measured warm scenarios")
    parts = [text(44, 52, "What does one Diary block cost locally?", 31, weight="700"),
             text(44, 85, "Measured MCP transport + storage; model thinking and writing are not included.", 19, MUTED)]
    chart_y, chart_h = 205, 330
    token_max = math.ceil(max(g["summary"]["cycle_serialized_input_plus_result_tokens"]["mean"] for g in groups) / 1000) * 1000
    time_max = math.ceil(max(g["summary"]["cycle_elapsed_ms"]["p95_nearest_rank"] for g in groups) / 5 + 1) * 5
    panels = [(90, 540, "Added serialized tokens / block", "cycle_serialized_input_plus_result_tokens", token_max, 1000, TEAL),
              (800, 500, "Warm MCP time / block (ms)", "cycle_elapsed_ms", time_max, 5, ORANGE)]
    for left, width, heading, field, maximum, step, color in panels:
        parts.append(text(left, 144, heading, 23, weight="700"))
        parts.append(text(left, 174, "Start → one milestone → complete", 16, MUTED))
        for tick in range(0, maximum + 1, step):
            y = chart_y + chart_h * (1 - tick / maximum)
            parts.extend([line(left, y, left + width, y), text(left - 14, y + 6, f"{tick:,}", 16, MUTED, "end")])
        for index, group in enumerate(groups):
            x = left + width * (index + 0.5) / len(groups)
            value = group["summary"][field]["mean"]
            y = chart_y + chart_h * (1 - value / maximum)
            parts.append(f'<rect x="{x-39}" y="{y}" width="78" height="{chart_y+chart_h-y}" rx="5" fill="{color}"/>')
            label = f"{value:,.0f}" if field == "cycle_serialized_input_plus_result_tokens" else f"{value:.2f}"
            label_y = y - 13
            if field == "cycle_elapsed_ms":
                label_y = chart_y + chart_h * (1 - group["summary"][field]["p95_nearest_rank"] / maximum) - 13
            parts.extend([text(x, label_y, label, 19, color, "middle", "700"),
                          text(x, chart_y + chart_h + 29, f'{group["prompt_tokens"]:,}', 18, INK, "middle")])
            if field == "cycle_elapsed_ms":
                p95 = group["summary"][field]["p95_nearest_rank"]
                py = chart_y + chart_h * (1 - p95 / maximum)
                parts.extend([line(x, y, x, py, INK, 2), line(x - 9, py, x + 9, py, INK, 2)])
        parts.append(text(left + width / 2, 601, "Original prompt length (o200k_base tokens)", 17, MUTED, "middle"))
    n = sum(g["blocks"] for g in groups)
    parts.extend([text(44, 649, f"{n} blocks · warm connection · bars = mean · time whiskers = empirical p95", 18, INK),
                  text(44, 681, "No Diary: 0 added Diary tokens and 0 Diary calls by definition — not zero total task cost.", 18, INK),
                  text(44, 713, "Token counts are JSON serialization proxies, not provider billing. Single-host synthetic workload.", 17, MUTED)])
    return document("Measured Diary MCP overhead", "Two charts show mean serialized token and warm MCP latency costs for three synthetic prompt lengths. Omitting Diary adds no Diary-specific bookkeeping, but total task cost is not measured here.", parts)


def render_recall(data, manifest):
    if data.get("status") != "complete" or data.get("actual_calls") != 39:
        raise ValueError("Refusing to plot an incomplete recall experiment")
    checkpoints = manifest["checkpoints_history_tokens"]
    parts = [text(44, 52, "Can a fresh continuation recover the current requirements?", 29, weight="700"),
             text(44, 85, "Exact-code recall failure after controlled context resets · lower is better", 19, MUTED)]
    top, height = 205, 330
    for left, arm, heading, color in ((95, "summary", "Without Diary: rolling summary", ORANGE),
                                      (810, "diary", "With Diary: recovered record", TEAL)):
        width = 500
        parts.extend([text(left, 139, heading, 23, weight="700"), text(left, 173, "Requirement recall failure (%)", 17, MUTED)])
        for tick in range(0, 101, 20):
            y = top + height * (1 - tick / 100)
            parts.extend([line(left, y, left + width, y), text(left - 14, y + 6, tick, 16, MUTED, "end")])
        points = []
        annotations = []
        for checkpoint in checkpoints:
            samples = [c for c in data["calls"] if c["arm"] == arm and c["checkpoint_tokens"] == checkpoint]
            if len(samples) != len(manifest["seeds"]):
                raise ValueError("Missing paired recall samples")
            rates = [c["metrics"]["requirement_failure_rate"] * 100 for c in samples]
            mean = statistics.fmean(rates)
            x = left + width * (checkpoint - min(checkpoints)) / (max(checkpoints) - min(checkpoints))
            y = top + height * (1 - mean / 100)
            points.append(f"{x},{y}")
            lo, hi = [top + height * (1 - r / 100) for r in (min(rates), max(rates))]
            annotations.append(line(x, lo, x, hi, color, 3))
            for rate in rates:
                cy = top + height * (1 - rate / 100)
                annotations.append(f'<circle cx="{x}" cy="{cy}" r="5" fill="#f6f9fa" stroke="{color}" stroke-width="2"/>')
            annotations.extend([text(x, max(top + 22, y - 18), f"{mean:.1f}%", 18, color, "middle", "700"),
                                text(x, top + height + 29, f"{checkpoint//1000}k", 18, INK, "middle")])
        parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="3"/>')
        parts.extend(annotations)
        parts.append(text(left + width / 2, 602, "Cumulative synthetic history tokens (o200k_base)", 16, MUTED, "middle"))
    parts.extend([text(44, 649, f'{manifest["model"]} · {manifest["reasoning_effort"]} effort · 3 histories · means + observed seed ranges', 18, INK),
                  text(44, 681, "Lines connect measured checkpoints, not a fitted forgetting law. Task requirements also grow: 32 → 48 → 64 → 80.", 17, INK),
                  text(44, 713, "Harness-managed memory; unequal context lengths. Not natural compaction or a universal model-memory score.", 17, MUTED)])
    return document("Diary versus rolling-summary recall pilot", "Side-by-side curves plot exact requirement recall failure against cumulative synthetic history tokens. The experiment compares harness-managed external records and rolling summaries, not native model memory.", parts)


def render_writer(data):
    if not data.get("complete") or len(data.get("samples", [])) != 9:
        raise ValueError("Refusing to plot incomplete journal-generation results")
    parts = [text(44, 52, "The model's journal-writing stage has a real cost", 31, weight="700"),
             text(44, 85, "One batched generation + three actual MCP writes per block; original task already completed.", 18, MUTED)]
    specs = [(90, "Reported model output tokens", "tokens", TEAL),
             (800, "Added controlled stage time (seconds)", "seconds", ORANGE)]
    top, height, width = 205, 330, 510
    for left, title, metric, color in specs:
        values = []
        maxima = []
        for group in data["groups"]:
            summary = group["summary"]
            stats = summary["provider_usage"]["output_tokens"] if metric == "tokens" else summary["controlled_stage_wall_ms"]
            divisor = 1 if metric == "tokens" else 1000
            values.append(stats["mean"] / divisor)
            maxima.append(stats["maximum"] / divisor)
        step = 100 if metric == "tokens" else max(5, math.ceil(max(maxima) / 5))
        maximum = max(step, math.ceil(max(maxima) / step + 0.5) * step)
        parts.extend([text(left, 144, title, 23, weight="700"), text(left, 174, "3 samples per prompt-length scenario", 17, MUTED)])
        for tick in range(0, maximum + 1, step):
            y = top + height * (1 - tick / maximum)
            parts.extend([line(left, y, left + width, y), text(left - 14, y + 6, tick, 16, MUTED, "end")])
        for i, (value, high) in enumerate(zip(values, maxima)):
            x = left + width * (i + 0.5) / 3
            y = top + height * (1 - value / maximum)
            high_y = top + height * (1 - high / maximum)
            parts.extend([f'<rect x="{x-39}" y="{y}" width="78" height="{top+height-y}" rx="5" fill="{color}"/>',
                          line(x, y, x, high_y, INK, 2), line(x - 9, high_y, x + 9, high_y, INK, 2),
                          text(x, high_y - 14, f"{value:.1f}", 19, color, "middle", "700"),
                          text(x, top + height + 29, ("80", "800", "4,000")[i], 18, INK, "middle")])
        parts.append(text(left + width / 2, 602, "Original prompt length (o200k_base tokens)", 17, MUTED, "middle"))
    parts.extend([text(44, 649, f'{data["model"]} · {data["reasoning_effort"]} effort · bars = mean · whiskers = maximum of 3 samples', 18, INK),
                  text(44, 681, "Without Diary: no optional bookkeeping stage. This does not compare total task time or rework saved.", 17, INK),
                  text(44, 713, "Input/cache/reasoning counters are reported separately in the data. Output is not the full per-call token budget.", 17, MUTED)])
    return document("Controlled model journal-writing overhead", "Measured output tokens and wall time for nine actual model-generated journal records. These are optional bookkeeping-stage costs, not end-to-end task costs.", parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp", type=Path)
    parser.add_argument("--recall-dir", type=Path)
    parser.add_argument("--writer", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "assets")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not any((args.mcp, args.recall_dir, args.writer)):
        parser.error("Provide at least one measured result source")
    if args.mcp:
        (args.output_dir / "mcp-overhead.svg").write_text(render_mcp(json.loads(args.mcp.read_text())), encoding="utf-8")
    if args.recall_dir:
        chart = render_recall(json.loads((args.recall_dir / "results.json").read_text()), json.loads((args.recall_dir / "manifest.json").read_text()))
        (args.output_dir / "recall-comparison.svg").write_text(chart, encoding="utf-8")
    if args.writer:
        (args.output_dir / "journal-generation.svg").write_text(render_writer(json.loads(args.writer.read_text())), encoding="utf-8")


if __name__ == "__main__":
    main()
