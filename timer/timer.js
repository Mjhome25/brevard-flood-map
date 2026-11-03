// Starts the GitHub "Update map" workflow. Needs secret GITHUB_TOKEN:
// a fine-grained token for Mjhome25/brevard-flood-map with Actions read/write.
const URL = "https://api.github.com/repos/Mjhome25/brevard-flood-map/actions/workflows/update-map.yml/dispatches";

export default {
  async scheduled(event, env, ctx) {
    const r = await fetch(URL, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "BrevardFloodMap-timer (+https://brevardemergency.com)",
      },
      body: JSON.stringify({ ref: "main" }),
    });
    if (r.status !== 204) throw new Error(`GitHub dispatch failed: ${r.status} ${await r.text()}`);
  },
};
