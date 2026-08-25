"use strict";

const byId = (id) => document.getElementById(id);
const percent = (value) => `${(value * 100).toFixed(1)}%`;
const dateTime = (value) => value ? new Intl.DateTimeFormat("ja-JP", {
  dateStyle: "medium", timeStyle: "short"
}).format(new Date(value)) : "—";

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderTicket(prediction) {
  const selection = byId("official-selection");
  selection.replaceChildren();
  prediction.actual.selection.forEach((horseId, index) => {
    if (index > 0) selection.append(node("span", "ticket-arrow", "→"));
    selection.append(node("span", "ticket-number", horseId));
  });
}

function renderRanking(prediction) {
  const body = byId("ranking-body");
  body.replaceChildren();
  prediction.runners.forEach((runner) => {
    const row = document.createElement("tr");
    const rank = document.createElement("td");
    rank.append(node("span", "rank-pill", String(runner.predicted_rank)));
    row.append(rank);
    row.append(node("td", "", runner.horse_id));
    row.append(node("td", "probability", percent(runner.win_probability)));
    row.append(node("td", "probability", percent(runner.top3_probability)));
    body.append(row);
  });
}

function renderShadows(prediction) {
  const groups = byId("shadow-groups");
  groups.replaceChildren();
  const grouped = new Map();
  prediction.shadow_portfolios.forEach((portfolio) => {
    const portfolios = grouped.get(portfolio.generator) || [];
    portfolios.push(portfolio);
    grouped.set(portfolio.generator, portfolios);
  });
  for (const [generator, portfolios] of grouped) {
    const group = node("section", "shadow-group");
    group.append(node("h3", "", generator === "baseline" ? "ベースライン" : "ペースシナリオ"));
    const strategies = [...new Set(portfolios.map((row) => row.strategy))];
    const ticketCounts = [...new Set(portfolios.map((row) => row.ticket_count))];
    const matrix = node("div", "shadow-matrix");
    matrix.style.setProperty("--strategy-count", String(strategies.length));
    matrix.append(node("span", "matrix-label", "点数"));
    strategies.forEach((strategy) => {
      matrix.append(node("span", "matrix-heading", strategy.replaceAll("_", " ")));
    });
    ticketCounts.forEach((ticketCount) => {
      matrix.append(node("span", "matrix-label", `${ticketCount}点・0円`));
      strategies.forEach((strategy) => {
        const portfolio = portfolios.find(
          (row) => row.ticket_count === ticketCount && row.strategy === strategy
        );
        const cell = node("div", "matrix-cell");
        if (portfolio) {
          const track = node("div", "bar-track");
          const fill = node("div", "bar-fill");
          fill.style.width = `${Math.min(100, portfolio.cumulative_probability * 100)}%`;
          track.append(fill);
          cell.append(track);
          cell.append(node("span", "shadow-value", percent(portfolio.cumulative_probability)));
        } else {
          cell.append(node("span", "shadow-value", "—"));
        }
        matrix.append(cell);
      });
    });
    group.append(matrix);
    groups.append(group);
  }
}

function renderPrediction(prediction) {
  if (!prediction) {
    byId("prediction-view").hidden = true;
    byId("empty-prediction").hidden = false;
    return;
  }
  byId("prediction-view").hidden = false;
  byId("empty-prediction").hidden = true;
  byId("race-title").textContent = prediction.race_id;
  byId("scheduled-at").textContent = dateTime(prediction.scheduled_at);
  byId("context-scheduled").textContent = dateTime(prediction.scheduled_at);
  byId("context-frozen").textContent = dateTime(prediction.frozen_at);
  byId("context-model").textContent = prediction.model_version;
  byId("context-input").textContent = prediction.input_data_version;
  renderTicket(prediction);
  const winner = prediction.runners[0];
  byId("winner-number").textContent = winner.predicted_rank;
  byId("winner-id").textContent = winner.horse_id;
  byId("winner-probability").textContent = percent(winner.win_probability);
  renderRanking(prediction);
  renderShadows(prediction);
}

function renderValidation(walkForward) {
  byId("validation").hidden = !walkForward;
  byId("empty-validation").hidden = Boolean(walkForward);
  if (!walkForward) return;
  byId("metric-races").textContent = String(walkForward.evaluation_race_count);
  byId("metric-model").textContent = percent(walkForward.model.top1_accuracy);
  byId("metric-uniform").textContent = percent(walkForward.uniform.top1_accuracy);
  byId("metric-ece").textContent = walkForward.expected_calibration_error.toFixed(3);
}

function renderWin5(win5) {
  const section = byId("win5");
  section.hidden = !win5;
  if (!win5) return;
  const legs = byId("win5-legs");
  legs.replaceChildren();
  win5.legs.forEach((leg, index) => {
    const item = node("article", "win5-leg");
    item.append(node("span", "win5-leg-number", `第${index + 1}対象`));
    item.append(node("strong", "", leg.selected_horse_id));
    item.append(node("small", "", leg.race_id));
    item.append(node("b", "", percent(leg.selected_win_probability)));
    legs.append(item);
  });
  byId("win5-probability").textContent = percent(win5.joint_probability);
  byId("win5-assumption").textContent = win5.independence_assumption;
}

async function loadState() {
  const reload = byId("reload");
  reload.disabled = true;
  byId("error").hidden = true;
  try {
    const response = await fetch("/api/v1/state", {cache: "no-store"});
    if (!response.ok) throw new Error(`読み込みに失敗しました（${response.status}）`);
    const state = await response.json();
    if (!state.is_valid) throw new Error("監査済みデータではありません");
    byId("context-policy").textContent = state.actual_purchase_policy;
    renderPrediction(state.prediction);
    renderWin5(state.win5);
    renderValidation(state.walk_forward);
    byId("loading").hidden = true;
    byId("app").hidden = false;
  } catch (error) {
    byId("loading").hidden = true;
    byId("app").hidden = true;
    byId("error").textContent = error instanceof Error ? error.message : "読み込みに失敗しました";
    byId("error").hidden = false;
  } finally {
    reload.disabled = false;
  }
}

byId("reload").addEventListener("click", loadState);
loadState();
