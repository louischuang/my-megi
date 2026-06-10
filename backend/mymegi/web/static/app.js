const state = {
  contactsQuery: "",
  companyClassification: "",
  regionClassification: "",
  industryClassification: "",
  selectedCardId: null,
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

const parseDraft = (value) => {
  if (!value) return {};
  if (typeof value === "object") return value;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
};

const listValue = (value) => (Array.isArray(value) ? value.filter(Boolean).join(", ") : value || "");
const splitList = (value) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
const classificationText = (classifications) => {
  const values = [
    ...(classifications?.company || []),
    ...(classifications?.region || []),
    ...(classifications?.industry || []),
  ];
  return values.length ? values.join(", ") : "";
};

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
      (item) => {
        const draft = parseDraft(item.extractedData);
        const draftText = draft.name
          ? `${draft.name}${draft.company?.englishName ? ` · ${draft.company.englishName}` : ""}`
          : "";
        return `
        <article class="list-item">
          <div>
            <strong>${escapeHtml(item.fileName)}</strong>
            <span>${fileSize(item.fileSizeBytes)} · ${formatDate(item.createdAt)}</span>
            ${
              item.errorMessage
                ? `<small class="error-text">${escapeHtml(item.errorMessage)}</small>`
                : draftText
                  ? `<small class="draft-text">${escapeHtml(draftText)}</small>`
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
            <button class="ghost compact" type="button" data-structure-card="${escapeHtml(item.id)}">
              整理
            </button>
            <button class="ghost compact" type="button" data-review-card="${escapeHtml(item.id)}">
              審核
            </button>
          </div>
        </article>
      `;
      },
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

async function structureCard(cardId, button) {
  button.disabled = true;
  button.textContent = "整理中";
  try {
    await fetchJson(`/api/cards/${cardId}/structure`, { method: "POST" });
    await refreshAll();
  } catch (error) {
    console.error(error);
    await loadCards();
  }
}

function setReviewState(text) {
  document.querySelector("#review-state").textContent = text;
}

function setReviewField(name, value) {
  const field = document.querySelector(`#review-form [name="${name}"]`);
  if (field) field.value = value ?? "";
}

function renderCardPreview(card) {
  const preview = document.querySelector("#card-preview");
  if (card.mimeType?.startsWith("image/")) {
    preview.innerHTML = `<img src="${card.previewUrl || card.fileUrl}" alt="${escapeHtml(card.fileName)}" />`;
    return;
  }
  if (card.mimeType === "application/pdf") {
    preview.innerHTML = `<iframe src="${card.fileUrl}" title="${escapeHtml(card.fileName)}"></iframe>`;
    return;
  }
  preview.innerHTML = `<div class="empty">無法預覽此檔案</div>`;
}

async function reviewCard(cardId) {
  setReviewState("載入中");
  const card = await fetchJson(`/api/cards/${cardId}`);
  const draft = parseDraft(card.extractedData);
  const context = card.uploadContext || {};
  state.selectedCardId = card.id;

  document.querySelector("#review-empty").hidden = true;
  document.querySelector("#review-workbench").hidden = false;
  document.querySelector("#ocr-text").value = card.ocrText || "";
  renderCardPreview(card);

  setReviewField("cardId", card.id);
  setReviewField("name", draft.name);
  setReviewField("englishName", draft.englishName);
  setReviewField("title", draft.title);
  setReviewField("companyName", draft.company?.name);
  setReviewField("companyEnglishName", draft.company?.englishName);
  setReviewField("emails", listValue(draft.emails));
  setReviewField("mobiles", listValue(draft.mobiles));
  setReviewField("phones", listValue(draft.phones));
  setReviewField("fax", listValue(draft.fax));
  setReviewField("taxId", draft.company?.taxId);
  setReviewField("industry", draft.company?.industry);
  setReviewField("addressRaw", draft.address?.raw);
  setReviewField("addressEnglishRaw", draft.address?.englishRaw);
  setReviewField("country", draft.address?.country);
  setReviewField("city", draft.address?.city);
  setReviewField("district", draft.address?.district);
  setReviewField("companyClassifications", listValue(draft.classifications?.company));
  setReviewField("regionClassifications", listValue(draft.classifications?.region));
  setReviewField(
    "industryClassifications",
    listValue(draft.classifications?.industry?.length ? draft.classifications.industry : [draft.company?.industry]),
  );
  setReviewField("metAt", context.metAt);
  setReviewField("metOn", context.metOn);
  setReviewField("note", context.note || draft.notes);
  setReviewState(card.status);
  document.querySelector("#review").scrollIntoView({ behavior: "smooth", block: "start" });
}

function reviewPayload(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  return {
    name: values.name,
    englishName: values.englishName || null,
    title: values.title || null,
    company: {
      name: values.companyName || null,
      englishName: values.companyEnglishName || null,
      taxId: values.taxId || null,
      industry: values.industry || null,
    },
    emails: splitList(values.emails),
    phones: splitList(values.phones),
    mobiles: splitList(values.mobiles),
    fax: splitList(values.fax),
    website: null,
    address: {
      raw: values.addressRaw || null,
      englishRaw: values.addressEnglishRaw || null,
      country: values.country || null,
      city: values.city || null,
      district: values.district || null,
    },
    classifications: {
      company: splitList(values.companyClassifications),
      region: splitList(values.regionClassifications),
      industry: splitList(values.industryClassifications || values.industry),
    },
    metAt: values.metAt || null,
    metOn: values.metOn || null,
    note: values.note || null,
  };
}

async function loadContacts() {
  const params = new URLSearchParams({ limit: "20" });
  if (state.contactsQuery) params.set("q", state.contactsQuery);
  if (state.companyClassification) {
    params.set("companyClassification", state.companyClassification);
  }
  if (state.regionClassification) {
    params.set("regionClassification", state.regionClassification);
  }
  if (state.industryClassification) {
    params.set("industryClassification", state.industryClassification);
  }
  const data = await fetchJson(`/api/contacts?${params.toString()}`);
  const body = document.querySelector("#contacts-body");
  if (!data.items.length) {
    body.innerHTML = `<tr><td colspan="5"><div class="empty">尚無聯絡人資料</div></td></tr>`;
    return;
  }
  body.innerHTML = data.items
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.name)}</td>
          <td>${escapeHtml(item.company)}</td>
          <td>${escapeHtml(item.title)}</td>
          <td>${escapeHtml(classificationText(item.classifications))}</td>
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
  status.textContent = "上傳與辨識中";
  try {
    const result = await fetchJson("/api/cards/upload", {
      method: "POST",
      body: new FormData(form),
    });
    form.reset();
    document.querySelector("#file-meta").textContent = "JPG、PNG、WEBP、PDF，最大 20MB";
    status.textContent = result.status === "needs_review" ? "待審核" : result.status;
    await refreshAll();
    if (result.cardId) {
      await reviewCard(result.cardId);
    }
  } catch (error) {
    status.textContent = "失敗";
    console.error(error);
  }
});

document.querySelector("#refresh-cards").addEventListener("click", loadCards);

document.querySelector("#cards-list").addEventListener("click", async (event) => {
  const extractButton = event.target.closest("[data-extract-card]");
  if (extractButton) {
    await extractCard(extractButton.dataset.extractCard, extractButton);
    return;
  }
  const structureButton = event.target.closest("[data-structure-card]");
  if (structureButton) {
    await structureCard(structureButton.dataset.structureCard, structureButton);
    return;
  }
  const reviewButton = event.target.closest("[data-review-card]");
  if (reviewButton) {
    await reviewCard(reviewButton.dataset.reviewCard);
  }
});

document.querySelector("#review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const cardId = new FormData(form).get("cardId");
  setReviewState("儲存中");
  try {
    await fetchJson(`/api/cards/${cardId}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reviewPayload(form)),
    });
    setReviewState("已儲存");
    await refreshAll();
  } catch (error) {
    setReviewState("儲存失敗");
    console.error(error);
  }
});

document.querySelector("#contact-search").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  state.contactsQuery = form.get("q").trim();
  state.companyClassification = form.get("companyClassification").trim();
  state.regionClassification = form.get("regionClassification").trim();
  state.industryClassification = form.get("industryClassification").trim();
  await loadContacts();
});

refreshAll().catch((error) => {
  console.error(error);
});
