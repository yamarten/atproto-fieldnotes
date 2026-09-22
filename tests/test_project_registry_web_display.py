"""Exercise the display boundary without a browser or external services."""
from pathlib import Path
import shutil
import subprocess
import unittest


class WebDisplayTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for Web display checks")
    def test_labels_preserve_machine_values_raw_data_and_escaping(self):
        path = Path(__file__).resolve().parents[1] / "scripts/project_registry_web/static/app.js"
        # Run the real render helpers; omit only page startup and supply browser storage.
        source = path.read_text().rsplit('\ndocument.querySelectorAll(".primary-nav', 1)[0]
        harness = r'''
const assert = require("node:assert/strict");
const location = {search:""};
const localStorage = {getItem: () => null};
const prompt = () => "";
const document = {};
'''
        checks = r'''
for (const [group, values] of Object.entries(filterOptionFallbacks)) {
  for (const value of values) assert.notEqual(displayLabel(value), value, `${group}: ${value}`);
}
assert.match(enumOptions(["active", "stalled"], "stalled"), /value="stalled" selected>活動停滞<\/option>/);
assert.match(multiEnumOptions(["active", "stalled"], "active,stalled"), /value="active" selected>通常追跡/);
assert.match(pill("candidate", "candidate"), /class="pill candidate">候補/);
assert.match(renderExplanation("merge_candidate", {conclusion:"none", rule:"", supporting_signals:[], counter_signals:[]}), /data-explain-topic="merge_candidate"/);
assert.equal(displayLabel("unrecognized_future_value"), "unrecognized_future_value");
assert.equal(displayLabel("owned_nsid:app.example.post"), "帰属NSID: app.example.post");
assert.equal(json({status:"active", summary:"Keep original <text>"}), '<pre>{\n  &quot;status&quot;: &quot;active&quot;,\n  &quot;summary&quot;: &quot;Keep original &lt;text&gt;&quot;\n}</pre>');
const unsafe = '<img src=x onerror=alert(1)>';
assert.ok(!pill(unsafe).includes('<img'));
assert.ok(!enumOptions([unsafe], unsafe).includes('<img'));
assert.equal(scheduleMessage("running enrich --all"), "実行中: 通常追跡中の全件調査");
assert.equal(scheduleMessage("scheduled step failed: sync github; resume pending steps manually"), "失敗: GitHubから取り込み。保留中の処理を手動で再開してください。");
assert.match(apiErrorMessage({code:"stale_project", message:"revision mismatch"}), /再読込/);
assert.match(apiErrorMessage({code:"validation_error", message:"future diagnostic"}), /入力内容.*future diagnostic/);
const originalNote = "service accepted by the configured trust policy";
assert.equal(decisionNote({kind:"block_identifier", note:originalNote}), originalNote);
assert.equal(decisionNote({kind:"confirm_identifier", note:originalNote, metadata:{source:"trust_policy"}}), "設定された信頼規則でサービスを採用");
const oauth = renderOAuthClient({metadata_url:"https://example.test/client.json", scope:"atproto repo:app.example.post?action=delete", analysis:{status:"parsed", permissions:[{kind:"repo", collection:["app.example.post"], action:["delete"]}]}});
assert.match(oauth, /操作: 削除/);
assert.match(oauth, /atproto repo:app.example.post\?action=delete/);
state.screen = "review";
state.reviewView = "promotion";
state.promotionView = "inbox";
const score = {total: 12, components: {service_evidence: 14, promotion_blockers: -2, recent_activity: 0}};
const candidate = {score_breakdown: score, service_evidence_group: "confirmed", priority_group: "normal",
  candidate_visibility: {group:"eligible"}, attention: {group:"unreviewed"},
  atproto_usage: {source:"none", role:"unknown", reason:"routine explanation"}};
const compact = promotionCardDetails(candidate);
assert.match(compact, /スコア 12/);
assert.match(compact, /atproto利用 未評価/);
assert.ok(!compact.includes("routine explanation"));
assert.ok(!compact.includes("通常の優先度"));
assert.ok(!compact.includes("紹介候補一覧の対象"));
assert.match(renderPromotionScore(score), /紹介材料の不足<\/dt><dd>-2/);
assert.match(renderPromotionScore(score), /<dd>\+14/);
assert.ok(!renderPromotionScore(score).includes("最近の活動"));
const waiting = promotionCardDetails({...candidate, priority_group:"lowered", priority_reason:unsafe,
  candidate_visibility:{group:"deferred"}, attention:{group:"changed"},
  atproto_usage:{source:"operator", role:"unknown", reason:"調査中のため不明"},
  active_deferral:{reason:"公開待ち", expires_at:"2026-10-22T00:00:00Z"}});
assert.match(waiting, /調査中のため不明/);
assert.match(waiting, /公開待ち/);
assert.match(waiting, /根拠変化・再確認/);
assert.match(waiting, /低い優先度/);
assert.ok(!waiting.includes("<img"));
assert.match(waiting, /&lt;img/);
state.promotionView = "technical";
const technical = promotionCardDetails({...candidate, technical_interest_level:"high", technical_interest_score:80,
  signals:[{kind:"technical_insight_candidate", details:{technical_interest_summary:"独自の連携方式"}}]});
assert.match(technical, /技術評価 高い · 80/);
assert.match(technical, /独自の連携方式/);
assert.ok(!renderServiceObservations([]).includes("取得失敗"));
assert.match(renderServiceObservations([{fetch_error:"timeout"}]), /取得失敗は提供終了を示しません/);
'''
        result = subprocess.run([shutil.which("node"), "-"], input=harness + source + checks,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
