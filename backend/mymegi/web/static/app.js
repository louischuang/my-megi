const state = {
  contactsQuery: "",
};

const formatDate = (value) => {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-Hant", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
};

const fileSize = (bytes) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }
  return response.json();
}

async function loadDashboard() {
  const data = await fetchJson("/api/dashboard");
  document.querySelector("#metric-contacts").textContent = data.contacts;
  document.querySelector("#metric-companies").textContent = data.companies;
  document.querySelector("#metric-cards").textContent = data.cards;
  document.querySelector("#metric-pending").textContent = data.pendingCards;
}

async function loadCards() {
  const container = document.querySelector("#cards-list");
  const data = await fetchJson("/api/cards?limit=8");
  if (!data.items.length) {
    container.innerHTML = `<div class="empty">尚無匯入名片</div>`;
    return;
  }
  container.innerHTML = data.items
    .map(
      (item) => `
        <article class="list-item">
          <div>
            <strong>${escapeHtml(item.fileName)}</strong>
            <span>${fileSize(item.fileSizeBytes)} · ${formatDate(item.createdAt)}</span>
            ${
              item.errorMessage
                ? `<small class="error-text">${escapeHtml(item.errorMessage)}</small>`
                : item.ocrPreview
                  ? `<small>${escapeHtml(item.ocrPreview)}</small>`
                  : ""
            }
          </div>
          <div class="item-actions">
            <span class="badge">${escapeHtml(item.status)}</span>
            <button class="ghost compact" type="button" data-extract-card="${escapeHtml(item.id)}">
              辨識
            </button>
          </div>
        </article>
      `,
    )
    .join("");
}

async function extractCard(cardId, button) {
  button.disabled = true;
  button.textContent = "辨識中";
  try {
    await fetchJson(`/api/cards/${cardId}/extract`, { method: "POST" });
    await refreshAll();
  } catch (error) {
    console.error(error);
    await loadCards();
  }
}

async function loadContacts() {
  const params = new URLSearchParams({ limit: "20" });
  if (state.contactsQuery) params.set("q", state.contactsQuery);
  const data = await fetchJson(`/api/contacts?${params.toString()}`);
  const body = document.querySelector("#contacts-body");
  if (!data.items.length) {
    body.innerHTML = `<tr><td colspan="4"><div class="empty">尚無聯絡人資料</div></td></tr>`;
    return;
  }
  body.innerHTML = data.items
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.name)}</td>
          <td>${escapeHtml(item.company)}</td>
          <td>${escapeHtml(item.title)}</td>
          <td>${formatDate(item.createdAt)}</td>
        </tr>
      `,
    )
    .join("");
}

async function refreshAll() {
  await Promise.all([loadDashboard(), loadCards(), loadContacts()]);
}

document.querySelector("#card-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  document.querySelector("#file-meta").textContent = file
    ? `${file.name} · ${fileSize(file.size)}`
    : "JPG、PNG、WEBP、PDF，最大 20MB";
});

document.querySelector("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.querySelector("#upload-state");
  status.textContent = "上傳中";
  try {
    await fetchJson("/api/cards/upload", {
      method: "POST",
      body: new FormData(form),
    });
    form.reset();
    document.querySelector("#file-meta").textContent = "JPG、PNG、WEBP、PDF，最大 20MB";
    status.textContent = "完成";
    await refreshAll();
  } catch (error) {
    status.textContent = "失敗";
    console.error(error);
  }
});

document.querySelector("#refresh-cards").addEventListener("click", loadCards);

document.querySelector("#cards-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-extract-card]");
  if (!button) return;
  await extractCard(button.dataset.extractCard, button);
});

document.querySelector("#contact-search").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.contactsQuery = new FormData(event.currentTarget).get("q").trim();
  await loadContacts();
});

refreshAll().catch((error) => {
  console.error(error);
});
