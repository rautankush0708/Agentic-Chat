export async function synthesizeSpeech(text) {
  const res = await fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    throw new Error(`TTS request failed with status ${res.status}`);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}
