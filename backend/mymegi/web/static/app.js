const state = {
  contactsQuery: "",
  companyClassification: "",
  regionClassification: "",
  industryClassification: "",
  selectedCardId: null,
};

const formatDate = (value) => {
  if (!value) return "";
  const date = new Date(value);
  const day = [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
  const time = [
    String(date.getHours()).padStart(2, "0"),
    String(date.getMinutes()).padStart(2, "0"),
    String(date.getSeconds()).padStart(2, "0"),
  ].join(":");
  return `${day} ${time}`;
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
const percentText = (value) => (value == null ? "未評估" : `${Math.round(Number(value) * 100)}%`);
const statusText = (value) =>
  ({
    completed: "已入庫",
    needs_review: "待審核",
    processing: "處理中",
    pending: "待處理",
    failed: "失敗",
    done: "已辨識",
  })[value] || value || "未知";

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try {
      const payload = JSON.parse(text);
      message = payload.detail || payload.message || text;
    } catch {
      message = text;
    }
    throw new Error(message || `Request failed: ${response.status}`);
  }
  return response.json();
}

function openModal(name) {
  const modal = document.querySelector(`#${name}-modal`);
  if (!modal) return;
  modal.hidden = false;
  document.body.classList.add("modal-open");
}

function closeModal(name) {
  const modal = document.querySelector(`#${name}-modal`);
  if (!modal) return;
  modal.hidden = true;
  if (!document.querySelector(".modal:not([hidden])")) {
    document.body.classList.remove("modal-open");
  }
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
        const companyName = draft.company?.name || draft.company?.englishName || "";
        const draftText = [draft.name, companyName].filter(Boolean).join(" · ");
        const fileNames = [item.fileName, item.backFileName].filter(Boolean).join(" / ");
        return `
        <article class="list-item">
          <div class="item-header">
            <span class="badge">${escapeHtml(statusText(item.reviewStatus))}</span>
            <strong title="${escapeHtml(fileNames)}">${escapeHtml(fileNames)}</strong>
          </div>
          <div class="item-row">
            <div class="item-meta">
              <span>${escapeHtml(formatDate(item.createdAt))}</span>
              <span>信心度 ${escapeHtml(percentText(item.confidence))}</span>
              <span>辨識 ${escapeHtml(statusText(item.recognitionStatus))}</span>
            </div>
            <div class="item-actions">
              <button class="ghost compact icon-button" type="button" data-extract-card="${escapeHtml(item.id)}" title="重新辨識" aria-label="重新辨識">
                ↻
              </button>
              <button class="ghost compact icon-button" type="button" data-review-card="${escapeHtml(item.id)}" title="審核" aria-label="審核">
                ✓
              </button>
            </div>
          </div>
          ${
            item.errorMessage
              ? `<small class="error-text">${escapeHtml(item.errorMessage)}</small>`
              : draftText
                ? `<small class="draft-text">${escapeHtml(draftText)}</small>`
                : ""
          }
        </article>
      `;
      },
    )
    .join("");
}

async function extractCard(cardId, button) {
  button.disabled = true;
  button.textContent = "...";
  try {
    await fetchJson(`/api/cards/${cardId}/extract`, { method: "POST" });
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
  const sides = card.imageSides?.length
    ? card.imageSides
    : [{ side: "front", fileName: card.fileName, mimeType: card.mimeType, fileUrl: card.fileUrl, previewUrl: card.previewUrl }];
  preview.innerHTML = sides
    .map((side) => {
      const label = side.side === "back" ? "背面" : "正面";
      if (side.mimeType?.startsWith("image/")) {
        return `<figure class="card-preview"><figcaption>${label}</figcaption><img src="${side.previewUrl || side.fileUrl}" alt="${escapeHtml(side.fileName)}" /></figure>`;
      }
      if (side.mimeType === "application/pdf") {
        return `<figure class="card-preview"><figcaption>${label}</figcaption><iframe src="${side.fileUrl}" title="${escapeHtml(side.fileName)}"></iframe></figure>`;
      }
      return `<figure class="card-preview"><figcaption>${label}</figcaption><div class="empty">無法預覽此檔案</div></figure>`;
    })
    .join("");
  if (sides.length) {
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
  setReviewField("extraNotes", card.extraNotes || draft.extraNotes);
  setReviewState(card.status);
  openModal("review");
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
    extraNotes: values.extraNotes || null,
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
    ? file.name
    : "JPG、PNG、WEBP、HEIC、HEIF、PDF，最大 20MB";
});

document.querySelector("#card-back-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  document.querySelector("#back-file-meta").textContent = file
    ? file.name
    : "選填，最多再加一張";
});

document.querySelector("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.querySelector("#upload-state");
  status.textContent = "上傳與辨識中";
  try {
    const formData = new FormData(form);
    const backFile = formData.get("backFile");
    if (backFile instanceof File && !backFile.name) {
      formData.delete("backFile");
    }
    const result = await fetchJson("/api/cards/upload", {
      method: "POST",
      body: formData,
    });
    form.reset();
    document.querySelector("#file-meta").textContent = "JPG、PNG、WEBP、HEIC、HEIF、PDF，最大 20MB";
    document.querySelector("#back-file-meta").textContent = "選填，最多再加一張";
    status.textContent = result.status === "needs_review" ? "待審核" : result.status;
    await refreshAll();
    if (result.cardId) {
      await reviewCard(result.cardId);
    }
  } catch (error) {
    status.textContent = error.message ? `失敗：${error.message}` : "失敗";
    console.error(error);
  }
});

document.querySelector("#refresh-cards").addEventListener("click", loadCards);

document.querySelector("#open-contacts").addEventListener("click", async () => {
  await loadContacts();
  openModal("contacts");
});

document.querySelectorAll("[data-close-modal]").forEach((button) => {
  button.addEventListener("click", () => closeModal(button.dataset.closeModal));
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  closeModal("review");
  closeModal("contacts");
});

document.querySelector("#cards-list").addEventListener("click", async (event) => {
  const extractButton = event.target.closest("[data-extract-card]");
  if (extractButton) {
    await extractCard(extractButton.dataset.extractCard, extractButton);
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
