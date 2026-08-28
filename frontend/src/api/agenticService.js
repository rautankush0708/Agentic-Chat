export async function handleAgent(queryRequest) {
  const res = await fetch("/api/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(queryRequest),
  });
  if (!res.ok) {
    throw new Error(`Agent request failed with status ${res.status}`);
  }
  return res.json();
}
