$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$data = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'apps\web\public\examples\pressure-v1.json') | ConvertFrom-Json
$moment = $data.insights | Where-Object { $_.insight_id -eq $data.audio.insight_id }
if (-not $moment) { throw 'Matching public example moment is missing' }
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voice.SelectVoice('Microsoft Zira Desktop')
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    $voice.SetOutputToWaveFile((Join-Path $root 'apps\web\public\examples\pressure-en.wav'), $format)
    $voice.Speak($moment.narratives.'en-GB'.fan.body)
} finally {
    $voice.Dispose()
}
