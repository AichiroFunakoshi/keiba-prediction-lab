"use strict";

const byId = (id) => document.getElementById(id);
let currentState = null;
let selectedVenueIndex = 0;
const percent = (value) => `${(value * 100).toFixed(1)}%`;
const marketLabel = (version) => {
  const match = version.match(/market-log-pool-v\d+-w(\d+)$/);
  return match ? `モデル${100 - Number(match[1])}%・オッズ${Number(match[1])}%` : "市場混合";
};
const smallPercent = (value) => {
  const percentage = value * 100;
  if (percentage === 0) return "0%";
  if (percentage < 0.01) return `${percentage.toFixed(3)}%`;
  if (percentage < 0.1) return `${percentage.toFixed(2)}%`;
  return `${percentage.toFixed(1)}%`;
};
const dateTime = (value) => value ? new Intl.DateTimeFormat("ja-JP", {
  dateStyle: "medium", timeStyle: "short"
}).format(new Date(value)) : "—";

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function runnerDisplayMap(runnerDisplay = []) {
  return new Map(runnerDisplay.map((item) => [item.horse_id, item]));
}

function oddsText(display) {
  if (!display || display.win_odds == null) return "単勝 未取得／人気 未取得";
  const popularity = display.popularity == null ? "人気未取得" : `${display.popularity}番人気${display.popularity_source === "odds_order" ? "相当" : ""}`;
  return `単勝${display.win_odds.toFixed(1)}倍・${popularity}`;
}
function oddsBadge(display) {
  const badge = node("small", "odds-badge", oddsText(display));
  badge.title = `${dateTime(display?.odds_observed_at)}取得${display?.popularity_source === "odds_order" ? "／単勝オッズ順の参考値・同オッズは同順位" : "／JRA掲載人気順"}`;
  return badge;
}
function trioSelection(prediction, displayById) {
  return [...prediction.trio.selection].sort((a, b) =>
    (displayById.get(a)?.horse_number ?? 999) - (displayById.get(b)?.horse_number ?? 999) || a.localeCompare(b));
}
function renderTicket(prediction, displayById) {
  const selection = byId("official-selection");
  selection.replaceChildren();
  trioSelection(prediction, displayById).forEach((horseId, index) => {
    if (index > 0) selection.append(node("span", "ticket-arrow", "－"));
    const display = displayById.get(horseId);
    const number = node(
      "span", display ? `ticket-number frame-${display.frame_number}` : "ticket-number",
      display ? String(display.horse_number) : horseId
    );
    if (display) number.title = `${display.horse_name}（馬ID: ${horseId}）`;
    const horse = node("span", "number-with-odds");
    horse.append(number, oddsBadge(display));
    selection.append(horse);
  });
}

function renderRanking(prediction, displayById) {
  const body = byId("ranking-body");
  body.replaceChildren();
  const visibleRunners = prediction.runners.slice(0, 8);
  byId("ranking-summary").textContent = prediction.runners.length > 8
    ? `全${prediction.runners.length}頭中、1着確率上位8頭を表示`
    : `全${prediction.runners.length}頭`;
  visibleRunners.forEach((runner) => {
    const display = displayById.get(runner.horse_id);
    const row = document.createElement("tr");
    const rank = document.createElement("td");
    rank.append(node("span", "rank-pill", String(runner.predicted_rank)));
    row.append(rank);
    const horseNumber = node(
      "span",
      display ? `runner-number frame-${display.frame_number}` : "runner-number",
      display ? String(display.horse_number) : "—"
    );
    const horseNumberCell = document.createElement("td");
    horseNumberCell.append(horseNumber);
    row.append(horseNumberCell);
    const horseName = node("td", "runner-name", display?.horse_name || runner.horse_id);
    if (display) horseName.title = `馬ID: ${runner.horse_id}`;
    row.append(horseName);
    row.append(node("td", "", display?.win_odds != null ? `${display.win_odds.toFixed(1)}倍` : "未取得"));
    row.append(node("td", "", display?.popularity != null ? `${display.popularity}番人気${display.popularity_source === "odds_order" ? "相当" : ""}` : "未取得"));
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

function renderPrediction(prediction, runnerDisplay = []) {
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
  const isMarketBlend = prediction.model_version.includes("market-log-pool");
  byId("context-model").textContent = isMarketBlend
    ? `${marketLabel(prediction.model_version)}（発走前オッズ反映）`
    : prediction.model_version.includes("ability-v5") ? "独自能力予測（オッズ不使用）" : prediction.model_version;
  byId("context-model").title = isMarketBlend ? prediction.model_version : "";
  byId("context-input").textContent = prediction.input_data_version;
  const displayById = runnerDisplayMap(runnerDisplay);
  renderTicket(prediction, displayById);
  byId("trio-probability").textContent = `推定的中率 ${percent(prediction.trio.probability)} ／ ${prediction.trio.frozen_at ? "発走前固定 " + dateTime(prediction.trio.frozen_at) : "保存予測から算出した参考候補"}`;
  const winner = prediction.runners[0];
  const winnerDisplay = displayById.get(winner.horse_id);
  const winnerNumber = byId("winner-number");
  winnerNumber.className = winnerDisplay
    ? `horse-number frame-${winnerDisplay.frame_number}`
    : "horse-number";
  winnerNumber.textContent = winnerDisplay ? winnerDisplay.horse_number : winner.predicted_rank;
  byId("winner-label").textContent = winnerDisplay ? "予測1位の馬" : "予測1位の馬ID";
  byId("winner-id").textContent = winnerDisplay?.horse_name || winner.horse_id;
  byId("winner-id").title = winnerDisplay ? `馬ID: ${winner.horse_id}` : "";
  byId("winner-odds").textContent = oddsText(winnerDisplay);
  byId("winner-probability").textContent = percent(winner.win_probability);
  renderRanking(prediction, displayById);
  renderShadows(prediction);
  renderExplanations(prediction, displayById);
}


function factorLabel(factor) {
  if (factor.feature.endsWith("missing")) {
    return `${factor.label.replace("欠測", "情報")}（${factor.value ? "未取得" : "取得済み"}）`;
  }
  if (factor.feature === "surface_changed") return `前走の路面（${factor.value ? "今回と異なる" : "今回と同じ・不明時は代替値"}）`;
  if (factor.feature === "distance_change_km") return `前走との距離差 ${Math.round(factor.value * 1000)}m`;
  if (factor.feature === "carried_weight_change") return `前走との斤量差 ${factor.value.toFixed(1)}kg`;
  const countLabels = {log_horse_starts: "取得済みの出走数", log_jockey_starts: "取得済み騎乗数", log_trainer_starts: "取得済み管理馬出走数", log_horse_surface_track_condition_starts: "同路面・馬場の履歴数"};
  return countLabels[factor.feature] || factor.label;
}

function renderExplanations(prediction, displayById) {
  const data = currentState?.prediction_explanations?.[prediction.race_id];
  byId("explanations").hidden = !data;
  const body = byId("explanation-rows");
  body.replaceChildren();
  if (!data) return;
  prediction.runners.forEach((runner) => {
    const entry = data[runner.horse_id];
    if (!entry) return;
    const display = displayById.get(runner.horse_id);
    const card = node("article", "explanation-card");
    card.append(oddsBadge(display));
    card.append(node("strong", "", `${runner.predicted_rank}位　${display ? `${display.horse_number} ${display.horse_name}` : runner.horse_id}`));
    const positive = entry.factors.filter((f) => f.contribution > 0.000001).slice(0, 3);
    const negative = entry.factors.filter((f) => f.contribution < -0.000001).slice(0, 2);
    card.append(node("p", "", `評価を押し上げ：${positive.map(factorLabel).join("、") || "目立つ項目なし"}`));
    card.append(node("p", "", `評価を押し下げ：${negative.map(factorLabel).join("、") || "目立つ項目なし"}`));
    const missing = [entry.recent_form_missing ? "保存済み近走なし" : `保存済み履歴${entry.history_starts}走`, entry.body_weight_missing ? "当日馬体重未取得" : "当日馬体重取得済み"];
    card.append(node("small", "", missing.join(" ／ ")));
    body.append(card);
  });
}

function compactTicket(selection, target, displayById = new Map()) {
  target.replaceChildren();
  selection.forEach((horseId, index) => {
    if (index > 0) target.append(node("span", "compact-arrow", "－"));
    const display = displayById.get(horseId);
    const number = node(
      "span", display ? `compact-number frame-${display.frame_number}` : "compact-number",
      display ? String(display.horse_number) : horseId
    );
    if (display) {
      number.title = `${display.horse_name}（馬ID: ${horseId}）`;
    }
    const horse = node("span", "number-with-odds");
    horse.append(number, oddsBadge(display));
    target.append(horse);
  });
}

function showDetail(prediction, runnerDisplay = []) {
  document.body.classList.remove("overview-mode");
  byId("dashboard").hidden = true;
  byId("detail-app").hidden = false;
  byId("back-overview").hidden = !currentState?.race_day;
  renderPrediction(prediction, runnerDisplay);
  renderValidation(currentState?.walk_forward || null);
  window.scrollTo(0, 0);
}

function renderVenue(raceDay, venueIndex) {
  selectedVenueIndex = venueIndex;
  const venue = raceDay.venues[venueIndex];
  byId("race-title").textContent = `${venue.venue} 全レース`;
  byId("venue-tabs").querySelectorAll("button").forEach((button, index) => {
    button.setAttribute("aria-selected", String(index === venueIndex));
    button.tabIndex = index === venueIndex ? 0 : -1;
  });
  const rows = byId("race-rows");
  rows.style.setProperty("--race-count", venue.races.length);
  rows.replaceChildren();
  venue.races.forEach((race) => {
    const prediction = race.prediction;
    const winner = prediction.runners[0];
    const displayById = runnerDisplayMap(race.runner_display || []);
    const winnerDisplay = displayById.get(winner.horse_id);
    const row = node("button", "ledger-row");
    row.type = "button";
    row.setAttribute("aria-label", `${venue.venue} ${race.race_number}Rの詳細を見る`);
    row.append(node("strong", "race-number", `${race.race_number}R`));
    row.append(node("time", "race-time", new Intl.DateTimeFormat("ja-JP", {
      hour: "2-digit", minute: "2-digit"
    }).format(new Date(prediction.scheduled_at))));
    const winnerCell = node("span", "ledger-winner");
    const winnerMark = node(
      "b", winnerDisplay ? `winner-mark frame-${winnerDisplay.frame_number}` : "winner-mark",
      winnerDisplay ? String(winnerDisplay.horse_number) : `${winner.predicted_rank}位`
    );
    winnerCell.append(winnerMark);
    const winnerName = node("strong", "", winnerDisplay?.horse_name || winner.horse_id);
    if (winnerDisplay) winnerName.title = `馬ID: ${winner.horse_id}`;
    winnerCell.append(winnerName);
    winnerCell.append(oddsBadge(winnerDisplay));
    row.append(winnerCell);
    const probabilityCell = node("span", "ledger-probability-wrap");
    probabilityCell.append(node("b", "ledger-probability", percent(winner.win_probability)));
    probabilityCell.append(node(
      "small", "", prediction.model_version.includes("market-log-pool")
        ? marketLabel(prediction.model_version) : "独立予測"
    ));
    row.append(probabilityCell);
    const ticket = node("span", "compact-ticket ledger-ticket");
    compactTicket(trioSelection(prediction, displayById), ticket, displayById);
    const ticketCell = node("span", "ledger-selections");
    ticketCell.append(ticket);
    const trioLabel = node("small", "ledger-wide", `推定的中率 ${percent(prediction.trio.probability)}`);
    trioLabel.title = prediction.trio.frozen_at
      ? `三連複候補の固定：${dateTime(prediction.trio.frozen_at)}`
      : "保存済み確率から算出した三連複参考候補。過去の正式購入記録とは別です。";
    ticketCell.append(trioLabel);
    row.append(ticketCell);
    const detail = node("span", "detail-link", "詳細");
    row.append(detail);
    row.addEventListener("click", () => {
      showDetail(prediction, race.runner_display || []);
    });
    rows.append(row);
  });
}

function renderDashboard(raceDay) {
  document.body.classList.add("overview-mode");
  byId("detail-app").hidden = true;
  byId("dashboard").hidden = false;
  byId("dashboard-toolbar").hidden = false;
  byId("race-ledger").hidden = false;
  byId("back-overview").hidden = true;
  byId("dashboard-date").textContent = new Intl.DateTimeFormat("ja-JP", {
    dateStyle: "full"
  }).format(new Date(`${raceDay.race_date}T00:00:00+09:00`));
  byId("scheduled-at").textContent = "開催日予測";
  const tabs = byId("venue-tabs");
  tabs.replaceChildren();
  raceDay.venues.forEach((venue, index) => {
    const tab = node("button", "venue-tab", venue.venue);
    tab.type = "button";
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", "race-ledger");
    tab.addEventListener("click", () => renderVenue(raceDay, index));
    tab.addEventListener("keydown", (event) => {
      const lastIndex = raceDay.venues.length - 1;
      let nextIndex = null;
      if (event.key === "ArrowLeft") nextIndex = index === 0 ? lastIndex : index - 1;
      if (event.key === "ArrowRight") nextIndex = index === lastIndex ? 0 : index + 1;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = lastIndex;
      if (nextIndex === null) return;
      event.preventDefault();
      renderVenue(raceDay, nextIndex);
      tabs.children[nextIndex].focus();
    });
    tabs.append(tab);
  });
  renderVenue(raceDay, Math.min(selectedVenueIndex, raceDay.venues.length - 1));
}

function renderWin5Only() {
  document.body.classList.remove("overview-mode");
  byId("detail-app").hidden = true;
  byId("dashboard").hidden = false;
  byId("dashboard-toolbar").hidden = true;
  byId("race-ledger").hidden = true;
  byId("back-overview").hidden = true;
  byId("race-title").textContent = "WIN5影予測";
  byId("scheduled-at").textContent = "研究用・0円";
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

function findRunnerDisplay(raceDay, raceId, horseId) {
  if (!raceDay) return null;
  for (const venue of raceDay.venues) {
    const race = venue.races.find((item) => item.prediction.race_id === raceId);
    if (!race) continue;
    return (race.runner_display || []).find((item) => item.horse_id === horseId) || null;
  }
  return null;
}

function renderWin5(win5, raceDay = null) {
  const section = byId("win5");
  section.hidden = !win5;
  if (!win5) return;
  const legs = byId("win5-legs");
  legs.replaceChildren();
  win5.legs.forEach((leg, index) => {
    const item = node("article", "win5-leg");
    item.append(node("span", "win5-leg-number", `対象${index + 1}`));
    const display = findRunnerDisplay(raceDay, leg.race_id, leg.selected_horse_id);
    const selection = node("div", "win5-selection");
    if (display) {
      selection.append(node(
        "span", `winner-mark frame-${display.frame_number}`,
        String(display.horse_number)
      ));
    }
    const horseName = node("strong", "", display?.horse_name || leg.selected_horse_id);
    if (display) horseName.title = `馬ID: ${leg.selected_horse_id}`;
    selection.append(horseName);
    item.append(selection);
    item.append(oddsBadge(display));
    item.append(node("small", "", leg.race_id));
    item.append(node("b", "", percent(leg.selected_win_probability)));
    legs.append(item);
  });
  byId("win5-probability").textContent = smallPercent(win5.joint_probability);
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
    currentState = state;
    const observed = (state.race_day?.venues || []).flatMap(v => v.races.flatMap(r => (r.runner_display || []).map(h => h.odds_observed_at))).filter(Boolean).sort();
    byId("market-status").textContent = observed.length ? `表示用オッズ：${dateTime(observed[observed.length-1])}取得。人気はJRA掲載値、「相当」は単勝順の参考値。予測計算とは別の情報です（自動更新なし）。` : "表示用オッズは未取得です。";
    const profileBox = byId("active-profile");
    const profile = state.active_prediction_profile;
    profileBox.hidden = !profile;
    profileBox.replaceChildren();
    if (profile) {
      const headline = node("div", "profile-headline");
      headline.append(node("strong", "", profile.market_weight === 0 ? "独自予測100％・オッズ不使用" : `独自モデル${percent(profile.model_weight)} ＋ オッズ${percent(profile.market_weight)}`));
      profileBox.append(headline);
      const details = node("details", "profile-details");
      details.append(node("summary", "", "予測条件・検証結果"));
      const explanation = node("div", "profile-explanation");
      explanation.append(node("p", "", profile.validation_summary));
      if (profile.comparison) {
        explanation.append(node("p", "", `市場比較版：独自モデル${percent(profile.comparison.model_weight)} ＋ オッズ${percent(profile.comparison.market_weight)}。${profile.comparison.validation_summary}`));
      }
      if (state.comparison_race_day) {
        const toggle = node("button", "", "市場比較版を見る");
        let showComparison = false;
        toggle.addEventListener("click", () => {
          showComparison = !showComparison;
          currentState = {...state,
            race_day: showComparison ? state.comparison_race_day : state.race_day,
            win5: showComparison ? state.comparison_win5 : state.win5,
            prediction_explanations: showComparison ? state.comparison_explanations : state.prediction_explanations};
          renderDashboard(currentState.race_day);
          renderWin5(currentState.win5, currentState.race_day);
          toggle.textContent = showComparison ? "主表示の予測に戻る" : "市場比較版を見る";
          headline.firstElementChild.textContent = showComparison
            ? (profile.comparison ? `市場比較：独自モデル${percent(profile.comparison.model_weight)} ＋ オッズ${percent(profile.comparison.market_weight)}` : "市場比較版")
            : (profile.market_weight === 0 ? "独自予測100％・オッズ不使用" : `独自モデル${percent(profile.model_weight)} ＋ オッズ${percent(profile.market_weight)}`);
        });
        headline.append(toggle);
      }
      if (profile.trio_shadow_enabled) explanation.append(node("p", "", "次回は三連複専用モデルのオッズ0％版・20％版も比較用に同時計算します。"));
      explanation.append(node("small", "", `設定：${profile.profile_id} ／ 各レースには予測時点の設定を表示`));
      details.append(explanation);
      if (state.trio_study) {
        const study = node("section", "trio-study");
        study.append(node("strong", "", "三連複専用モデルの開発検証"));
        const table = node("table", "");
        const header = node("tr", "");
        ["方式", "三連複的中", "確率誤差（小さいほど良い）"].forEach(t => header.append(node("th", "", t)));
        table.append(header);
        [["winner_v5", "現行の勝率方式"], ["trio_set", "新しい三連複学習"]].forEach(([key, label]) => {
          const score = state.trio_study.summary[key];
          const row = node("tr", "");
          [label, `${score.hits} / ${score.races}（${percent(score.accuracy)}）`, score.log_loss.toFixed(4)].forEach(t => row.append(node("td", "", t)));
          table.append(row);
        });
        study.append(table, node("small", "", state.trio_study.note));
        details.append(study);
      }
      profileBox.append(details);
    }
    byId("context-policy").textContent = "三連複1点100円の候補。順不同。投票機能はありません。";
    if (state.race_day) {
      renderDashboard(state.race_day);
      renderWin5(state.win5, state.race_day);
    } else if (state.prediction) {
      showDetail(state.prediction);
    } else if (state.win5) {
      renderWin5Only();
      renderWin5(state.win5);
    } else {
      showDetail(null);
    }
    byId("loading").hidden = true;
  } catch (error) {
    byId("loading").hidden = true;
    byId("dashboard").hidden = true;
    byId("detail-app").hidden = true;
    byId("error").textContent = error instanceof Error ? error.message : "読み込みに失敗しました";
    byId("error").hidden = false;
  } finally {
    reload.disabled = false;
  }
}

byId("reload").addEventListener("click", loadState);
byId("back-overview").addEventListener("click", () => {
  if (currentState?.race_day) renderDashboard(currentState.race_day);
});
loadState();
