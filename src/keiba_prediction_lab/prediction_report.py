"""Human-readable report for an integrity-checked prediction bundle."""

from pathlib import Path

from .bundle_audit import load_audited_prediction_bundle
from .evaluation import BET_TYPE_LABELS_JA
from .trifecta import TrifectaStrategy


_STRATEGY_LABELS = {
    TrifectaStrategy.SINGLE_WINNER_ANCHOR: "単一1着固定",
    TrifectaStrategy.MULTI_WINNER_SCENARIO: "複数1着シナリオ",
}


def _selection(values: tuple[str, ...]) -> str:
    return " → ".join(values)


def build_prediction_bundle_markdown(directory: str | Path) -> str:
    """Audit a saved bundle and render its pre-race forecasts as Markdown."""
    loaded = load_audited_prediction_bundle(directory)
    audit = loaded.audit
    bundle = loaded.bundle
    actual = bundle.actual_prediction
    ticket = actual.trifecta_tickets[0]
    lines = [
        f"# レース予測レポート: {audit.race_id}",
        "",
        "> 実購入候補は三連単1点100円だけです。3・5・10点および他の馬券種は、購入額0円の影予測です。",
        "",
        "## 固定情報",
        "",
        f"- 発走予定: {audit.scheduled_at.isoformat()}",
        f"- 予測固定: {audit.frozen_at.isoformat()}",
        f"- モデル: `{audit.model_version}`",
        f"- 入力データ版: `{audit.input_data_version}`",
        f"- 出走頭数: {audit.runner_count}頭",
        "",
        "## 実購入候補",
        "",
        "| 馬券種 | 買い目 | 購入額 |",
        "|---|---|---:|",
        f"| 三連単 | {_selection(ticket.selection)} | {ticket.stake_yen}円 |",
        "",
        "## 1着予測順位",
        "",
        "| 順位 | 馬ID | 勝率 | 3着内率 |",
        "|---:|---|---:|---:|",
    ]
    for row in sorted(actual.predictions, key=lambda item: item.predicted_rank):
        lines.append(
            f"| {row.predicted_rank} | {row.horse_id} | "
            f"{row.win_probability:.2%} | {row.top3_probability:.2%} |"
        )

    lines.extend([
        "",
        "## 三連単の影予測（購入しない）",
        "",
        "各行は同じ確率順位から作る反実仮想です。点数が増えても購入額は0円です。",
    ])
    for title, frozen in (
        ("基準モデル", bundle.baseline_shadow),
        ("ペース条件付きモデル", bundle.pace_shadow),
    ):
        lines.extend([
            "",
            f"### {title}",
            "",
            "| 戦略 | 点数 | 累積推定確率 | 買い目 | 購入額 |",
            "|---|---:|---:|---|---:|",
        ])
        for portfolio in frozen.forecast.shadow_portfolios:
            combinations = "<br>".join(
                _selection(row.selection) for row in portfolio.combinations
            )
            lines.append(
                f"| {_STRATEGY_LABELS[portfolio.strategy]} | "
                f"{portfolio.ticket_count} | {portfolio.cumulative_probability:.2%} | "
                f"{combinations} | 0円 |"
            )

    lines.extend([
        "",
        "## 全6馬券種の影予測（購入しない）",
        "",
        "| 馬券種 | 最上位候補 | 推定確率 | 購入額 |",
        "|---|---|---:|---:|",
    ])
    for candidate in bundle.bet_type_shadow.forecast.candidates:
        lines.append(
            f"| {BET_TYPE_LABELS_JA[candidate.bet_type]} | "
            f"{_selection(candidate.selection)} | {candidate.probability:.2%} | 0円 |"
        )
    lines.extend([
        "",
        "---",
        "",
        "このレポートは整合性監査を通過した保存済みファイルだけから生成されています。推定確率は的中を保証しません。",
    ])
    return "\n".join(lines) + "\n"
