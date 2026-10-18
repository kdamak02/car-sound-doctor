# Generates the English voice-over of the demo video, one WAV per clip, with the Windows speech engine.
# Run in PowerShell from the repository root:  .\docs\video\voiceover\make_voiceover.ps1
# Options:  -Voice "Microsoft Zira Desktop"  (female)  or  "Microsoft David Desktop"  (male, default if present);  -Rate -2..2 (0 = normal speed)
param([string]$Voice = "", [int]$Rate = 0)
Add-Type -AssemblyName System.Speech
$out = Split-Path -Parent $MyInvocation.MyCommand.Path
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$en = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'en-*' } | ForEach-Object { $_.VoiceInfo.Name }
if (-not $en) { Write-Host 'No English voice installed: Settings > Time & language > Speech > Add voices > English (United States).'; exit 1 }
Write-Host ('English voices on this PC: ' + ($en -join ', '))
if (-not $Voice) { $Voice = ($en | Where-Object { $_ -like '*David*' } | Select-Object -First 1); if (-not $Voice) { $Voice = $en[0] } }
$s.SelectVoice($Voice); $s.Rate = $Rate
Write-Host "Voice: $Voice, rate $Rate"
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$clips = @(
  @('1_intro', 20, 'Your car makes a strange noise... but which one, and is it serious? Car Sound Doctor answers that. The driver sends a voice note to a Telegram bot, and three AI agents listen to it, diagnose it with the manufacturer''s manuals, and guide the repair.'),
  @('2_telegram', 58, 'Our driver has a 2015 Renault Clio diesel. This morning, it won''t start. He records the noise and sends it to the bot. Like a mechanic, the bot asks for the car model, then for the problem in one sentence. First agent: listening. An Audio Spectrogram Transformer recognises a failed start, with medium confidence, and flags an unusual sound. Second agent: diagnosis. Two likely causes, the battery or the starter, with probabilities. To decide, it asks three simple checks, and each one cites its source: the Clio 4 manual, page 225.'),
  @('3_n8n_pass1', 47, 'Behind the scenes, n8n orchestrates the whole journey. Agent 1 returns the sound class and its top three: failed ignition at 48 percent, dead battery at 35 percent. Agent 2 is an LLM agent on Groq with one tool, rag search. It searches almost 16,000 passages from manuals and OBD codes, with hybrid search and a reranker. Then this node, code and not a prompt, checks every citation: a cause without a real passage is removed.'),
  @('4_answers', 10, 'The driver answers... and the diagnosis flips: the starter is now clearly on top.'),
  @('5_n8n_pass2', 29, 'On the second pass, there is no new audio: Agent 2 reads the answers. The starter is confirmed; the battery is ruled out, because the dashboard lights come on normally. The diagnosis passes the guardrails, then Agent 3 writes the repair guide.'),
  @('6_guide', 9, 'The guide gives the safety steps, and honestly states its limits.'),
  @('7_end', 25, 'To sum up, three principles. Traceable: every claim cites its source. Controlled: critical rules live in code, not in the prompt. Honest: when the system doesn''t know, it says so. Thank you.')
)
foreach ($c in $clips) {
  $file = Join-Path $out ($c[0] + '.wav')
  $s.SetOutputToWaveFile($file, $fmt); $s.Speak($c[2]); $s.SetOutputToNull()
  $sec = [math]::Round(((Get-Item $file).Length - 44) / 88200, 1)
  $flag = if ($sec -gt $c[1]) { '  TOO LONG: run again with -Rate 1' } else { '' }
  Write-Host ('{0,-14} {1,5} s for a {2} s clip{3}' -f $c[0], $sec, $c[1], $flag)
}
$s.Dispose()
Write-Host "Done: the WAV files are in $out. Import them in Clipchamp under each clip."
