#Requires -Version 7
$ErrorActionPreference = 'Stop'

# advisor-bridge/tests/fixtures/build.ps1 — run once, output committed.
#
# Synthesizes the eight fixtures used by Tasks 3-7 from the RECORDED SHAPES in
# SCHEMA.md. Nothing in this script reads, copies, or samples a real transcript.
# Every session id, path, marker string, and filler sentence below is invented.
#
# $side is deliberately UNTYPED and three-state: $true, $false, or $null meaning
# "omit the key entirely". The omitted-key record is the only thing that proves
# the renderer keeps a record unless isSidechain is exactly `$true` (implemented
# as `-eq $true` guarded by `continue`, not `-eq $false` and not `-ne $true`
# written directly as the keep condition - see the renderer's own comment for
# why), and a [bool] parameter cannot express the omitted-key case - $null
# would coerce to $false and write the key.
#
# $content is likewise untyped so a fixture can carry a bare STRING as
# message.content, not only a block array. Claude Code writes plain-string
# content for ordinary typed user messages, and Format-Turn has a dedicated
# branch for it; an [array] parameter would make that branch untestable.
function Rec([string]$type, $content, $side = $null) {
    $rec = [ordered]@{ type = $type }
    if ($null -ne $side) { $rec['isSidechain'] = [bool]$side }
    $rec['message'] = [ordered]@{ role = $type; content = $content }
    return ($rec | ConvertTo-Json -Depth 20 -Compress)
}
function Text($s)        { [ordered]@{ type = 'text';        text = $s } }
function Think($s)       { [ordered]@{ type = 'thinking';    thinking = $s } }
function Use($n, $i)     { [ordered]@{ type = 'tool_use';    name = $n; input = @{ cmd = $i } } }
function Res($s)         { [ordered]@{ type = 'tool_result'; content = $s } }

# Deterministic filler text of exactly $n characters, tagged so a human
# reading a diff can tell which fixture/turn a block of filler came from.
function Filler([int]$n, [string]$tag) {
    $unit = "invented filler content for $tag, not real transcript text. "
    $sb = [System.Text.StringBuilder]::new()
    while ($sb.Length -lt $n) { [void]$sb.Append($unit) }
    return $sb.ToString().Substring(0, $n)
}

$fixturesDir = $PSScriptRoot

# ---------------------------------------------------------------------------
# basic.jsonl — one user, one assistant, one attachment record. The attachment
# is built through the same Rec() helper as the turns (message.content with a
# text block) so that dropping it is proved by a type filter, not by the
# record being unrenderable in the first place.
# ---------------------------------------------------------------------------
$basic = @(
    (Rec 'user'       @(Text 'This is an ordinary invented user turn for the basic fixture.'))
    (Rec 'assistant'  @(Text 'This is an ordinary invented assistant reply for the basic fixture.'))
    (Rec 'attachment' @(Text 'ATTACHMENT-MARKER: invented hook-style payload a correct renderer must never emit.'))
)
$basic | Set-Content -Path (Join-Path $fixturesDir 'basic.jsonl') -Encoding utf8NoBOM

# ---------------------------------------------------------------------------
# sidechain.jsonl — three user records exercising all three isSidechain
# states: true (dropped), absent (kept), false (kept).
# ---------------------------------------------------------------------------
$sidechain = @(
    (Rec 'user' @(Text 'SIDECHAIN-MARKER: invented content of a sidechain-tagged turn a correct renderer must drop.') $true)
    (Rec 'user' @(Text 'This invented user turn has no isSidechain key at all and must be kept.') $null)
    (Rec 'user' @(Text 'This invented user turn has isSidechain explicitly false and must be kept.') $false)
)
$sidechain | Set-Content -Path (Join-Path $fixturesDir 'sidechain.jsonl') -Encoding utf8NoBOM

# ---------------------------------------------------------------------------
# caps.jsonl — 22 records total. Two "capped" exchanges (assistant record
# carrying thinking:900 chars + tool_use input:1200 chars, immediately
# followed by a user record carrying tool_result:5000 chars): one at records
# 5-6 (clearly mid-transcript, well outside any last-12 window over 22
# records), one at records 17-18 (inside the last-12 window, records 11-22).
# Everything else is short filler alternating user/assistant.
# ---------------------------------------------------------------------------
$caps = [System.Collections.Generic.List[string]]::new()
for ($i = 1; $i -le 20; $i++) {
    if ($i -eq 5) {
        $caps.Add((Rec 'assistant' @((Think (Filler 900 'caps-mid-thinking')), (Use 'Bash' (Filler 1200 'caps-mid-tool-input')))))
        $caps.Add((Rec 'user' @((Res (Filler 5000 'caps-mid-tool-result')))))
        continue
    }
    if ($i -eq 16) {
        $caps.Add((Rec 'assistant' @((Think (Filler 900 'caps-tail-thinking')), (Use 'Bash' (Filler 1200 'caps-tail-tool-input')))))
        $caps.Add((Rec 'user' @((Res (Filler 5000 'caps-tail-tool-result')))))
        continue
    }
    $role = if ($i % 2 -eq 1) { 'user' } else { 'assistant' }
    $caps.Add((Rec $role @((Text (Filler 100 "caps-filler-$i")))))
}
# Sanity: 18 filler iterations + 2 pairs (2 records each) = 22 records.
if ($caps.Count -ne 22) { throw "caps.jsonl: expected 22 records, built $($caps.Count)" }
$caps | Set-Content -Path (Join-Path $fixturesDir 'caps.jsonl') -Encoding utf8NoBOM

# ---------------------------------------------------------------------------
# long.jsonl — 40 records (turns), alternating user/assistant starting with
# user. Turn 1's message.content is a bare STRING (the shape Claude Code
# writes for an ordinary typed user message), ~500 chars, ending in the
# literal marker so survival proves the message was kept whole rather than
# head-truncated. Turns 2-40 are ~500-char text-block turns.
# ---------------------------------------------------------------------------
$long = [System.Collections.Generic.List[string]]::new()
$marker = 'FIRST-MESSAGE-MARKER-END'
$firstMessage = (Filler (500 - $marker.Length) 'long-first-message') + $marker
$long.Add((Rec 'user' $firstMessage))
for ($i = 2; $i -le 40; $i++) {
    $role = if ($i % 2 -eq 1) { 'user' } else { 'assistant' }
    $long.Add((Rec $role @((Text (Filler 500 "long-turn-$i")))))
}
if ($long.Count -ne 40) { throw "long.jsonl: expected 40 records, built $($long.Count)" }
$long | Set-Content -Path (Join-Path $fixturesDir 'long.jsonl') -Encoding utf8NoBOM

# ---------------------------------------------------------------------------
# oversized-tail.jsonl — 3 turns, the most recent (last record) a single
# text block of 200,000 chars.
# ---------------------------------------------------------------------------
$oversizedTail = @(
    (Rec 'user'      @((Text 'Turn 1: an ordinary short invented user message.')))
    (Rec 'assistant' @((Text 'Turn 2: an ordinary short invented assistant reply.')))
    (Rec 'user'      @((Text (Filler 200000 'oversized-tail'))))
)
$oversizedTail | Set-Content -Path (Join-Path $fixturesDir 'oversized-tail.jsonl') -Encoding utf8NoBOM

# ---------------------------------------------------------------------------
# truncated.jsonl — two valid records, then a third line cut mid-object
# (no closing brace) to simulate a write interrupted mid-flush.
# ---------------------------------------------------------------------------
$goodLine1 = Rec 'user' @((Text 'First valid record before the simulated interrupted write.'))
$goodLine2 = Rec 'assistant' @((Text 'Second valid record, still before the truncation point.'))
$thirdWhole = Rec 'user' @((Text 'This third record never finishes because the write was interrupted mid-object.'))
$thirdTruncated = $thirdWhole.Substring(0, $thirdWhole.Length - 20)
@($goodLine1, $goodLine2, $thirdTruncated) | Set-Content -Path (Join-Path $fixturesDir 'truncated.jsonl') -Encoding utf8NoBOM

# ---------------------------------------------------------------------------
# empty.jsonl — three attachment records and nothing else.
# ---------------------------------------------------------------------------
$empty = @(
    (Rec 'attachment' @((Text 'First invented attachment-only record.')))
    (Rec 'attachment' @((Text 'Second invented attachment-only record.')))
    (Rec 'attachment' @((Text 'Third invented attachment-only record.')))
)
$empty | Set-Content -Path (Join-Path $fixturesDir 'empty.jsonl') -Encoding utf8NoBOM

# ---------------------------------------------------------------------------
# leading-assistant.jsonl — an assistant record at record 0, with no user
# turn before it (e.g. an injected compact summary), followed by one ordinary
# user/assistant exchange. Proves $firstUserIdx is subtracted from
# $turnsRendered so leading assistant-only turns are not silently dropped
# from the count.
# ---------------------------------------------------------------------------
$leadingAssistant = @(
    (Rec 'assistant' @(Text 'Leading assistant turn with no user turn before it - e.g. an injected compact summary at record 0.'))
    (Rec 'user'      @(Text 'First real user turn.'))
    (Rec 'assistant' @(Text 'Reply to the first user turn.'))
)
$leadingAssistant | Set-Content -Path (Join-Path $fixturesDir 'leading-assistant.jsonl') -Encoding utf8NoBOM

Write-Host "Fixtures written to $fixturesDir"
