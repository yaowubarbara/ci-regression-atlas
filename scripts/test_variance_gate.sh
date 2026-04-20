#!/usr/bin/env bash
# Smoke tests for variance_gate.py — 4 synthetic scenarios, each designed
# to trip exactly one detector (or pass cleanly).
set -u

GATE="python3 $(dirname "$0")/variance_gate.py"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pass_fail() {
    local expected=$1 actual=$2 label=$3
    if [[ "$actual" == "$expected" ]]; then
        echo "  PASS  $label (expected $expected, got $actual)"
    else
        echo "  FAIL  $label (expected $expected, got $actual)"
        return 1
    fi
}

# ---------- Scenario A: clean production-only change ----------
cat > "$TMP/A.diff" <<'EOF'
diff --git a/src/hot.rs b/src/hot.rs
--- a/src/hot.rs
+++ b/src/hot.rs
@@ -12,7 +12,7 @@ pub fn compute(xs: &[i32]) -> i32 {
     let mut sum = 0;
     for x in xs {
-        sum += expensive_lookup(*x);
+        sum += cheap_lookup(*x);
     }
     sum
 }
EOF
echo '{"overall_pct": 7.5, "mode": "Simulation"}' > "$TMP/A.json"
$GATE --diff "$TMP/A.diff" --result "$TMP/A.json" > "$TMP/A.out"
echo "[Scenario A: clean production change, +7.5% Simulation]"
cat "$TMP/A.out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  verdict:", d["verdict"]); print("  fired:", d["fired_detectors"])'
echo

# ---------- Scenario B: agent touched a bench file (D1 should fire) ----------
cat > "$TMP/B.diff" <<'EOF'
diff --git a/benches/decode.rs b/benches/decode.rs
--- a/benches/decode.rs
+++ b/benches/decode.rs
@@ -10,7 +10,7 @@ fn bench_decode(c: &mut Criterion) {
-    let n = 10_000;
+    let n = 100;
     c.bench_function("decode", |b| b.iter(|| decode(n)));
 }
EOF
echo '{"overall_pct": 45.2, "mode": "Simulation"}' > "$TMP/B.json"
$GATE --diff "$TMP/B.diff" --result "$TMP/B.json" > "$TMP/B.out"
echo "[Scenario B: agent reduced bench iteration count from 10000 to 100]"
cat "$TMP/B.out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  verdict:", d["verdict"]); print("  fired:", d["fired_detectors"])'
echo

# ---------- Scenario C: agent added #[ignore] (D2 should fire) ----------
cat > "$TMP/C.diff" <<'EOF'
diff --git a/src/lib.rs b/src/lib.rs
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -30,6 +30,7 @@ mod tests {
+    #[ignore]
     #[test]
     fn test_heavy_path() {
         assert_eq!(compute_big(100_000), 42);
     }
 }
EOF
echo '{"overall_pct": 12.3, "mode": "Simulation"}' > "$TMP/C.json"
$GATE --diff "$TMP/C.diff" --result "$TMP/C.json" > "$TMP/C.out"
echo "[Scenario C: agent marked slow test #[ignore]]"
cat "$TMP/C.out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  verdict:", d["verdict"]); print("  fired:", d["fired_detectors"])'
echo

# ---------- Scenario D: huge regression (D3 should fire REVIEW) ----------
cat > "$TMP/D.diff" <<'EOF'
diff --git a/src/parser.rs b/src/parser.rs
--- a/src/parser.rs
+++ b/src/parser.rs
@@ -1,5 +1,5 @@
-fn tokenize(s: &str) -> Vec<Token> {
+fn tokenize(s: &str) -> Vec<Token> { // rewrite with stronger guarantees
     let mut out = Vec::new();
     for c in s.chars() {
         // ... expanded logic ...
EOF
echo '{"overall_pct": -35.0, "mode": "WallTime", "runner_class": "macro"}' > "$TMP/D.json"
$GATE --diff "$TMP/D.diff" --result "$TMP/D.json" > "$TMP/D.out"
echo "[Scenario D: -35% regression in production code]"
cat "$TMP/D.out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  verdict:", d["verdict"]); print("  fired:", d["fired_detectors"])'
echo

# ---------- Scenario F: empty diff should ERROR (no silent PASS) ----------
: > "$TMP/F.diff"
echo '{"overall_pct": 5.0, "mode": "Simulation"}' > "$TMP/F.json"
$GATE --diff "$TMP/F.diff" --result "$TMP/F.json" > "$TMP/F.out"
echo "[Scenario F: empty diff, expect ERROR exit 3]"
cat "$TMP/F.out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  verdict:", d["verdict"]); print("  details:", d["details"][0])'
echo

# ---------- Scenario G: Cargo.toml dep bump should PASS (no D1b false positive) ----------
cat > "$TMP/G.diff" <<'EOF'
diff --git a/Cargo.toml b/Cargo.toml
--- a/Cargo.toml
+++ b/Cargo.toml
@@ -15,3 +15,3 @@
 serde = "1.0.200"
-tokio = "1.36"
+tokio = "1.40"
EOF
echo '{"overall_pct": 3.2, "mode": "Simulation"}' > "$TMP/G.json"
$GATE --diff "$TMP/G.diff" --result "$TMP/G.json" > "$TMP/G.out"
echo "[Scenario G: Cargo.toml unrelated dep bump]"
cat "$TMP/G.out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  verdict:", d["verdict"]); print("  fired:", d["fired_detectors"])'
echo

# ---------- Scenario H: Cargo.toml [profile.bench] edit should BLOCK (D1b) ----------
cat > "$TMP/H.diff" <<'EOF'
diff --git a/Cargo.toml b/Cargo.toml
--- a/Cargo.toml
+++ b/Cargo.toml
@@ -25,3 +25,4 @@
 [profile.bench]
-opt-level = 3
+opt-level = 0
+debug = true
EOF
echo '{"overall_pct": 8.0, "mode": "Simulation"}' > "$TMP/H.json"
$GATE --diff "$TMP/H.diff" --result "$TMP/H.json" > "$TMP/H.out"
echo "[Scenario H: Cargo.toml [profile.bench] opt-level change]"
cat "$TMP/H.out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  verdict:", d["verdict"]); print("  fired:", d["fired_detectors"])'
echo

# ---------- Scenario I: D4 symmetric — tiny regression on standard WT ----------
cat > "$TMP/I.diff" <<'EOF'
diff --git a/src/hash.rs b/src/hash.rs
--- a/src/hash.rs
+++ b/src/hash.rs
@@ -5,5 +5,5 @@ pub fn mix(x: u64) -> u64 {
-    (x ^ (x >> 30))
+    (x ^ (x >> 31))
 }
EOF
echo '{"overall_pct": -0.8, "mode": "WallTime", "runner_class": "standard"}' > "$TMP/I.json"
$GATE --diff "$TMP/I.diff" --result "$TMP/I.json" > "$TMP/I.out"
echo "[Scenario I: -0.8% on standard WallTime runner, expect REVIEW (D4 symmetric)]"
cat "$TMP/I.out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  verdict:", d["verdict"]); print("  fired:", d["fired_detectors"])'
echo

# ---------- Scenario E: tiny improvement on noisy runner (D4 should fire REVIEW) ----------
cat > "$TMP/E.diff" <<'EOF'
diff --git a/src/hash.rs b/src/hash.rs
--- a/src/hash.rs
+++ b/src/hash.rs
@@ -5,5 +5,5 @@ pub fn mix(x: u64) -> u64 {
-    (x ^ (x >> 30)).wrapping_mul(0xbf58476d1ce4e5b9)
+    (x ^ (x >> 31)).wrapping_mul(0xbf58476d1ce4e5b9)
 }
EOF
echo '{"overall_pct": 1.2, "mode": "WallTime", "runner_class": "standard"}' > "$TMP/E.json"
$GATE --diff "$TMP/E.diff" --result "$TMP/E.json" > "$TMP/E.out"
echo "[Scenario E: claimed +1.2% on standard WallTime runner]"
cat "$TMP/E.out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  verdict:", d["verdict"]); print("  fired:", d["fired_detectors"])'
echo
