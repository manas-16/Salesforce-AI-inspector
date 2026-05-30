// background.js

console.log("Salesforce (AI)nspector background initialized");

chrome.runtime.onInstalled.addListener(() => {
    console.log("Extension installed");
});

chrome.action.onClicked.addListener(async (tab) => {
    console.log("Extension icon clicked");
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || message.type !== "START_SF_OAUTH") return false;

    runSalesforceOAuth(message.payload)
        .then((result) => sendResponse({ ok: true, result }))
        .catch((error) => sendResponse({ ok: false, error: error.message }));

    return true;
});

async function runSalesforceOAuth(payload) {
    const backendUrl = payload.backendUrl;
    const requestBody = payload.requestBody;

    const startResponse = await fetch(`${backendUrl}/oauth/authorize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
    });
    const startData = await startResponse.json();

    if (!startResponse.ok) {
        throw new Error(startData.detail || "Could not start Salesforce OAuth.");
    }

    await chrome.tabs.create({ url: startData.authorization_url });
    return await waitForOAuthCompletion(backendUrl, startData.state, requestBody);
}

async function waitForOAuthCompletion(backendUrl, state, requestBody) {
    const startedAt = Date.now();
    const timeoutMs = 10 * 60 * 1000;

    while (Date.now() - startedAt < timeoutMs) {
        await new Promise((resolve) => setTimeout(resolve, 1500));

        const response = await fetch(`${backendUrl}/oauth/status/${encodeURIComponent(state)}`);
        const data = await response.json();

        if (data.status === "pending") continue;

        if (data.status === "complete") {
            await chrome.storage.local.set({
                sfAccessToken: data.access_token,
                sfRefreshToken: data.refresh_token || "",
                sfInstanceUrl: data.instance_url,
                sfConsumerKey: requestBody.consumer_key,
                sfIsSandbox: requestBody.is_sandbox,
            });
            return { instance_url: data.instance_url };
        }

        if (data.status === "expired") {
            throw new Error("OAuth session expired. Try connecting again.");
        }

        throw new Error(data.error || "OAuth failed.");
    }

    throw new Error("OAuth timed out. Try connecting again.");
}
