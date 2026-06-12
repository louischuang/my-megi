const state = {
  currentUser: null,
  contactsQuery: "",
  companyClassification: "",
  regionClassification: "",
  industryClassification: "",
  selectedCardId: null,
  selectedContactId: null,
  cardReviewTab: "pending",
  toastTimer: null,
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
const isAutoReviewed = (item) => item.reviewStatus === "completed" && Number(item.confidence || 0) >= 0.9;
const statusText = (value) =>
  ({
    completed: "已入庫",
    needs_review: "待審核",
    processing: "處理中",
    pending: "待處理",
    failed: "失敗",
    done: "已辨識",
  })[value] || value || "未知";
const recognitionText = (value) =>
  ({
    done: "辨識成功",
    failed: "辨識失敗",
    processing: "辨識中",
    pending: "待辨識",
  })[value] || `辨識 ${statusText(value)}`;

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
    const error = new Error(message || `Request failed: ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function isSystemAdmin() {
  return state.currentUser?.role === "system_admin";
}

function showLogin() {
  state.currentUser = null;
  document.body.classList.remove("is-authenticated");
  document.querySelector("#login-screen").hidden = false;
}

function showAuthenticated(user) {
  state.currentUser = user;
  document.body.classList.add("is-authenticated");
  document.querySelector("#login-screen").hidden = true;
  document.querySelector("#current-user-label").textContent = `${user.displayName} · ${user.role}`;
  document.querySelectorAll(".admin-only").forEach((element) => {
    element.hidden = !isSystemAdmin();
  });
  document.querySelector('[data-main-tab="upload"]').hidden = isSystemAdmin();
  document.querySelector('[data-main-tab="contacts"]').hidden = isSystemAdmin();
  document.querySelector('[data-main-tab="api"]').hidden = isSystemAdmin();
  showMainTab(isSystemAdmin() ? "admin" : "upload");
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

function detailValue(value) {
  const text = Array.isArray(value) ? value.filter(Boolean).join(", ") : value;
  return escapeHtml(text || "未填寫");
}

function showMainTab(name) {
  if (isSystemAdmin() && name !== "admin") {
    name = "admin";
  }
  document.querySelectorAll("[data-main-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.mainPanel !== name;
  });
  document.querySelectorAll("[data-main-tab]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.mainTab === name);
  });
  if (name === "contacts") {
    loadContacts().catch((error) => console.error(error));
  }
  if (name === "api") {
    loadAccessTokens().catch((error) => console.error(error));
  }
  if (name === "admin") {
    refreshAdmin().catch((error) => console.error(error));
  }
}

function setUploadBusy(isBusy) {
  const panel = document.querySelector("#upload");
  const form = document.querySelector("#upload-form");
  panel.classList.toggle("is-uploading", isBusy);
  form.querySelectorAll("input, textarea, button").forEach((control) => {
    control.disabled = isBusy;
  });
}

function showUploadToast(confidence, autoConfirmed = false) {
  const toast = document.querySelector("#upload-toast");
  const message = document.querySelector("#upload-toast-message");
  message.textContent = `${autoConfirmed ? "已自動加入聯絡人，" : ""}辨識度 ${percentText(confidence)}`;
  toast.hidden = false;
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    toast.hidden = true;
  }, 5000);
}

function assignDroppedFile(input, files) {
  if (!files?.length) return;
  const transfer = new DataTransfer();
  transfer.items.add(files[0]);
  input.files = transfer.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

async function loadDashboard() {
  if (isSystemAdmin()) return;
  const data = await fetchJson("/api/dashboard");
  document.querySelector("#metric-contacts").textContent = data.contacts;
  document.querySelector("#metric-companies").textContent = data.companies;
  document.querySelector("#metric-cards").textContent = data.cards;
  document.querySelector("#metric-pending").textContent = data.pendingCards;
}

async function loadVersion() {
  try {
    const data = await fetchJson("/api/version");
    document.querySelectorAll("[data-app-version]").forEach((versionLabel) => {
      versionLabel.textContent = `v${data.version}`;
    });
  } catch (error) {
    console.error(error);
  }
}

async function loadCards() {
  if (isSystemAdmin()) return;
  const container = document.querySelector("#cards-list");
  const data = await fetchJson("/api/cards?limit=50");
  const items = data.items.filter((item) =>
    state.cardReviewTab === "completed" ? item.reviewStatus === "completed" : item.reviewStatus !== "completed",
  );
  if (!items.length) {
    container.innerHTML = `<div class="empty">尚無匯入名片</div>`;
    return;
  }
  container.innerHTML = items
    .map(
      (item) => {
        const draft = parseDraft(item.extractedData);
        const companyName = draft.company?.name || draft.company?.englishName || "";
        const draftText = [draft.name, companyName].filter(Boolean).join(" · ");
        const fileNames = [item.fileName, item.backFileName].filter(Boolean).join(" / ");
        const autoReviewed = isAutoReviewed(item);
        return `
        <article class="list-item">
          <div class="item-header">
            <span class="badge">${escapeHtml(autoReviewed ? "自動審核" : statusText(item.reviewStatus))}</span>
            <strong title="${escapeHtml(fileNames)}">${escapeHtml(fileNames)}</strong>
          </div>
          <div class="item-row">
            <div class="item-meta">
              <span>${escapeHtml(formatDate(item.createdAt))}</span>
              <span>信心度 ${escapeHtml(percentText(item.confidence))}</span>
              <span>${escapeHtml(recognitionText(item.recognitionStatus))}</span>
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

function cardPreviewHtml(sides) {
  if (!sides.length) {
    return `<div class="empty">無法預覽此檔案</div>`;
  }
  return sides
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
}

function renderCardPreview(card, target = "#card-preview") {
  const preview = document.querySelector(target);
  const sides = card.imageSides?.length
    ? card.imageSides
    : [{ side: "front", fileName: card.fileName, mimeType: card.mimeType, fileUrl: card.fileUrl, previewUrl: card.previewUrl }];
  preview.innerHTML = cardPreviewHtml(sides);
}

async function reviewCard(cardId) {
  document.querySelector("#review-title").textContent = "名片審核";
  setReviewState("載入中");
  const card = await fetchJson(`/api/cards/${cardId}`);
  const draft = parseDraft(card.extractedData);
  const context = card.uploadContext || {};
  state.selectedCardId = card.id;
  state.selectedContactId = null;

  document.querySelector("#review-empty").hidden = true;
  document.querySelector("#review-workbench").hidden = false;
  document.querySelector("#ocr-text").value = card.ocrText || "";
  renderCardPreview(card);

  setReviewField("cardId", card.id);
  setReviewField("contactId", "");
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

function contactMethodValues(contact, type) {
  return (contact.methods || [])
    .filter((method) => method.type === type)
    .map((method) => method.value);
}

function contactFaxValues(contact) {
  return (contact.methods || [])
    .filter((method) => method.type === "other" && /^FAX:/i.test(method.value || ""))
    .map((method) => method.value.replace(/^FAX:\s*/i, ""));
}

function firstAddress(contact) {
  return contact.addresses?.[0] || {};
}

function firstRelationshipNote(contact) {
  return contact.relationshipNotes?.[0] || {};
}

async function editContact(contactId) {
  closeModal("contact-detail");
  document.querySelector("#review-title").textContent = "聯絡人編輯";
  setReviewState("載入中");
  const contact = await fetchJson(`/api/contacts/${contactId}`);
  const address = firstAddress(contact);
  const relationship = firstRelationshipNote(contact);
  state.selectedCardId = contact.businessCard?.id || null;
  state.selectedContactId = contact.id;

  document.querySelector("#review-empty").hidden = true;
  document.querySelector("#review-workbench").hidden = false;
  document.querySelector("#ocr-text").value = contact.businessCard?.ocrText || "";
  renderCardPreview({ imageSides: contact.businessCard?.imageSides || [] });

  setReviewField("cardId", contact.businessCard?.id || "");
  setReviewField("contactId", contact.id);
  setReviewField("name", contact.name);
  setReviewField("englishName", contact.englishName);
  setReviewField("title", contact.title);
  setReviewField("companyName", contact.company?.name);
  setReviewField("companyEnglishName", contact.company?.englishName);
  setReviewField("emails", listValue(contactMethodValues(contact, "email")));
  setReviewField("mobiles", listValue(contactMethodValues(contact, "mobile")));
  setReviewField("phones", listValue(contactMethodValues(contact, "phone")));
  setReviewField("fax", listValue(contactFaxValues(contact)));
  setReviewField("taxId", contact.company?.taxId);
  setReviewField("industry", contact.company?.industry);
  setReviewField("addressRaw", address.raw);
  setReviewField("addressEnglishRaw", address.english);
  setReviewField("country", address.country);
  setReviewField("city", address.city);
  setReviewField("district", address.district);
  setReviewField("companyClassifications", listValue(contact.classifications?.company));
  setReviewField("regionClassifications", listValue(contact.classifications?.region));
  setReviewField("industryClassifications", listValue(contact.classifications?.industry));
  setReviewField("metAt", relationship.metAt);
  setReviewField("metOn", relationship.metOn);
  setReviewField("note", contact.notes || relationship.summary);
  setReviewField("extraNotes", contact.extraNotes);
  setReviewState("編輯中");
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
  if (isSystemAdmin()) return;
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
    body.innerHTML = `<tr><td colspan="6"><div class="empty">尚無聯絡人資料</div></td></tr>`;
    return;
  }
  body.innerHTML = data.items
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.name)}</td>
          <td>${escapeHtml(item.company)}</td>
          <td>${escapeHtml(item.title)}</td>
          <td class="classification-cell" title="${escapeHtml(classificationText(item.classifications))}">${escapeHtml(classificationText(item.classifications))}</td>
          <td>${formatDate(item.createdAt)}</td>
          <td>
            <div class="row-actions">
              <button class="bare-icon" type="button" data-view-contact="${escapeHtml(item.id)}" title="檢視" aria-label="檢視">
                <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 12s3-6 8-6 8 6 8 6-3 6-8 6-8-6-8-6Z"/><circle cx="12" cy="12" r="2.5"/></svg>
              </button>
              <button class="bare-icon danger" type="button" data-delete-contact="${escapeHtml(item.id)}" title="刪除" aria-label="刪除">
                <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 7h14"/><path d="M9 7V5h6v2"/><path d="m8 10 .5 9h7l.5-9"/><path d="M10.5 12.5v4M13.5 12.5v4"/></svg>
              </button>
            </div>
          </td>
        </tr>
      `,
    )
    .join("");
}

async function openContactsPanel() {
  showMainTab("contacts");
}

function renderContactDetail(contact) {
  const methods = contact.methods?.length
    ? contact.methods.map((method) => `${method.type}: ${method.value}`).join(", ")
    : "";
  const addresses = contact.addresses?.length
    ? contact.addresses.map((address) => [address.raw, address.english].filter(Boolean).join(" / ")).join(", ")
    : "";
  const relationshipNotes = contact.relationshipNotes?.length
    ? contact.relationshipNotes
        .map((note) => [note.metOn, note.metAt, note.summary].filter(Boolean).join(" · "))
        .join("\n")
    : "";
  const classifications = [
    ["公司分類", contact.classifications?.company],
    ["地區分類", contact.classifications?.region],
    ["產業分類", contact.classifications?.industry],
  ];
  document.querySelector("#contact-detail").innerHTML = `
    <section>
      <h3>基本資料</h3>
      <dl>
        <dt>姓名</dt><dd>${detailValue(contact.name)}</dd>
        <dt>英文名</dt><dd>${detailValue(contact.englishName)}</dd>
        <dt>職稱</dt><dd>${detailValue(contact.title)}</dd>
        <dt>建立時間</dt><dd>${detailValue(formatDate(contact.createdAt))}</dd>
      </dl>
    </section>
    <section>
      <h3>公司</h3>
      <dl>
        <dt>公司中文</dt><dd>${detailValue(contact.company?.name)}</dd>
        <dt>公司英文</dt><dd>${detailValue(contact.company?.englishName)}</dd>
        <dt>統編</dt><dd>${detailValue(contact.company?.taxId)}</dd>
        <dt>產業</dt><dd>${detailValue(contact.company?.industry)}</dd>
      </dl>
    </section>
    <section>
      <h3>聯絡方式與地址</h3>
      <dl>
        <dt>聯絡方式</dt><dd>${detailValue(methods)}</dd>
        <dt>地址</dt><dd>${detailValue(addresses)}</dd>
        <dt>來源名片</dt><dd>${detailValue(contact.businessCard?.fileName)}</dd>
      </dl>
    </section>
    <section>
      <h3>分類與備註</h3>
      <dl>
        ${classifications.map(([label, values]) => `<dt>${label}</dt><dd>${detailValue(values)}</dd>`).join("")}
        <dt>認識紀錄</dt><dd>${detailValue(relationshipNotes)}</dd>
        <dt>備註</dt><dd>${detailValue(contact.notes)}</dd>
        <dt>名片額外備註</dt><dd>${detailValue(contact.extraNotes)}</dd>
      </dl>
    </section>
    ${
      contact.businessCard?.imageSides?.length
        ? `<section class="detail-card-section">
            <h3>原始名片</h3>
            <div class="card-preview-grid detail-card-preview" id="contact-detail-card-preview"></div>
          </section>`
        : ""
    }
  `;
  if (contact.businessCard?.imageSides?.length) {
    renderCardPreview({ imageSides: contact.businessCard.imageSides }, "#contact-detail-card-preview");
  }
}

async function viewContact(contactId) {
  const contact = await fetchJson(`/api/contacts/${contactId}`);
  state.selectedContactId = contact.id;
  document.querySelector("#contact-edit-button").dataset.editContact = contact.id;
  renderContactDetail(contact);
  openModal("contact-detail");
}

async function deleteContact(contactId) {
  if (!window.confirm("確定刪除此聯絡人？刪除後此筆聯絡人會從列表隱藏。")) return;
  await fetchJson(`/api/contacts/${contactId}`, { method: "DELETE" });
  await refreshAll();
}

async function loadAccessTokens() {
  if (isSystemAdmin()) return;
  const data = await fetchJson("/api/access-tokens");
  const body = document.querySelector("#access-tokens-body");
  if (!data.items.length) {
    body.innerHTML = `<tr><td colspan="6"><div class="empty">尚無 API Access Token</div></td></tr>`;
    return;
  }
  body.innerHTML = data.items
    .map(
      (item) => `
      <tr>
        <td>${escapeHtml(item.name)}</td>
        <td><code>${escapeHtml(item.prefix)}</code></td>
        <td><span class="badge">${escapeHtml(item.status)}</span></td>
        <td>${escapeHtml(formatDate(item.lastUsedAt))}</td>
        <td>${escapeHtml(formatDate(item.createdAt))}</td>
        <td>
          <div class="row-actions">
            ${
              item.status === "active"
                ? `<button class="bare-icon danger" type="button" data-revoke-token="${escapeHtml(item.id)}" title="撤銷" aria-label="撤銷">×</button>`
                : ""
            }
          </div>
        </td>
      </tr>
    `,
    )
    .join("");
}

async function createAccessToken(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  const result = document.querySelector("#access-token-result");
  const tokenValue = document.querySelector("#access-token-value");
  result.hidden = true;
  tokenValue.textContent = "";
  const data = await fetchJson("/api/access-tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: values.name || "Default API Token" }),
  });
  tokenValue.textContent = data.item.token;
  result.hidden = false;
  await loadAccessTokens();
}

async function revokeAccessToken(tokenId) {
  if (!window.confirm("確定撤銷這組 API Access Token？")) return;
  await fetchJson(`/api/access-tokens/${tokenId}/revoke`, { method: "POST" });
  document.querySelector("#access-token-result").hidden = true;
  await loadAccessTokens();
}

async function loadUsers() {
  const data = await fetchJson("/api/users");
  const body = document.querySelector("#users-body");
  if (!data.items.length) {
    body.innerHTML = `<tr><td colspan="6"><div class="empty">尚無用戶</div></td></tr>`;
    return;
  }
  body.innerHTML = data.items
    .map(
      (user) => `
      <tr>
        <td>${escapeHtml(user.email)}</td>
        <td>${escapeHtml(user.displayName)}</td>
        <td>${escapeHtml(user.role)}</td>
        <td>${escapeHtml(user.status)}</td>
        <td>${escapeHtml(formatDate(user.lastLoginAt))}</td>
        <td>
          <div class="row-actions">
            ${
              user.status === "active"
                ? `<button class="bare-icon danger" type="button" data-disable-user="${escapeHtml(user.id)}" title="停用" aria-label="停用">×</button>`
                : `<button class="bare-icon" type="button" data-enable-user="${escapeHtml(user.id)}" title="啟用" aria-label="啟用">✓</button>`
            }
          </div>
        </td>
      </tr>
    `,
    )
    .join("");
}

async function loadLogoRecords() {
  const data = await fetchJson("/api/logo-records");
  const list = document.querySelector("#logo-records-list");
  if (!data.items.length) {
    list.innerHTML = `<div class="empty">尚無 Logo 紀錄</div>`;
    return;
  }
  list.innerHTML = data.items
    .map(
      (item) => `
      <article class="list-item">
        <div class="item-header">
          <span class="badge">${item.isActive ? "啟用" : "紀錄"}</span>
          <strong>${escapeHtml(item.fileName)}</strong>
        </div>
        <small>${escapeHtml(item.versionLabel || "")} ${escapeHtml(formatDate(item.createdAt))}</small>
      </article>
    `,
    )
    .join("");
}

async function refreshAdmin() {
  if (!isSystemAdmin()) return;
  await Promise.all([loadUsers(), loadLogoRecords()]);
}

async function refreshAll() {
  if (isSystemAdmin()) {
    await refreshAdmin();
    return;
  }
  await Promise.all([loadDashboard(), loadCards(), loadContacts()]);
}

async function initializeAuth() {
  try {
    const data = await fetchJson("/api/me");
    showAuthenticated(data.user);
    await refreshAll();
  } catch (error) {
    if (error.status === 401) {
      showLogin();
      return;
    }
    console.error(error);
    showLogin();
  }
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

document.querySelectorAll("[data-drop-zone]").forEach((zone) => {
  const input = zone.querySelector("input[type='file']");
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("is-dragging");
  });
  zone.addEventListener("dragleave", () => {
    zone.classList.remove("is-dragging");
  });
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("is-dragging");
    assignDroppedFile(input, event.dataTransfer.files);
  });
});

document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = Object.fromEntries(new FormData(form).entries());
  document.querySelector("#login-error").textContent = "";
  try {
    const data = await fetchJson("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    showAuthenticated(data.user);
    await refreshAll();
  } catch (error) {
    document.querySelector("#login-error").textContent = error.message || "登入失敗";
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  try {
    await fetchJson("/api/auth/logout", { method: "POST" });
  } catch (error) {
    console.error(error);
  }
  showLogin();
});

document.querySelector("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.querySelector("#upload-state");
  status.textContent = "上傳與辨識中";
  try {
    const formData = new FormData(form);
    setUploadBusy(true);
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
    status.textContent = result.autoConfirmed ? "已自動入庫" : result.status === "needs_review" ? "待審核" : result.status;
    showUploadToast(result.confidence ?? result.processing?.structure?.draft?.confidence ?? null, result.autoConfirmed);
    await refreshAll();
  } catch (error) {
    status.textContent = error.message ? `失敗：${error.message}` : "失敗";
    console.error(error);
  } finally {
    setUploadBusy(false);
  }
});

document.querySelector("#refresh-cards").addEventListener("click", loadCards);

document.querySelectorAll("[data-card-tab]").forEach((button) => {
  button.addEventListener("click", async () => {
    state.cardReviewTab = button.dataset.cardTab;
    document.querySelectorAll("[data-card-tab]").forEach((tab) => {
      const selected = tab === button;
      tab.classList.toggle("is-active", selected);
      tab.setAttribute("aria-selected", String(selected));
    });
    await loadCards();
  });
});

document.querySelectorAll("[data-close-modal]").forEach((button) => {
  button.addEventListener("click", () => closeModal(button.dataset.closeModal));
});

document.addEventListener("click", (event) => {
  const tabButton = event.target.closest("[data-main-tab]");
  if (!tabButton) return;
  showMainTab(tabButton.dataset.mainTab);
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  closeModal("review");
  closeModal("contact-detail");
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
  const formData = new FormData(form);
  const cardId = formData.get("cardId");
  const contactId = formData.get("contactId");
  setReviewState("儲存中");
  try {
    const url = contactId ? `/api/contacts/${contactId}` : `/api/cards/${cardId}/confirm`;
    await fetchJson(url, {
      method: contactId ? "PUT" : "POST",
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

document.querySelector("#contact-edit-button").addEventListener("click", async (event) => {
  const contactId = event.currentTarget.dataset.editContact;
  if (!contactId) return;
  await editContact(contactId);
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

document.querySelector("#contacts-body").addEventListener("click", async (event) => {
  const viewButton = event.target.closest("[data-view-contact]");
  if (viewButton) {
    await viewContact(viewButton.dataset.viewContact);
    return;
  }
  const deleteButton = event.target.closest("[data-delete-contact]");
  if (deleteButton) {
    await deleteContact(deleteButton.dataset.deleteContact);
  }
});

document.querySelector("#access-token-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await createAccessToken(event.currentTarget);
});

document.querySelector("#access-tokens-body").addEventListener("click", async (event) => {
  const revokeButton = event.target.closest("[data-revoke-token]");
  if (!revokeButton) return;
  await revokeAccessToken(revokeButton.dataset.revokeToken);
});

document.querySelector("#refresh-users").addEventListener("click", refreshAdmin);

document.querySelector("#user-create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = Object.fromEntries(new FormData(form).entries());
  await fetchJson("/api/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(values),
  });
  form.reset();
  await refreshAdmin();
});

document.querySelector("#users-body").addEventListener("click", async (event) => {
  const disableButton = event.target.closest("[data-disable-user]");
  if (disableButton) {
    await fetchJson(`/api/users/${disableButton.dataset.disableUser}/disable`, { method: "POST" });
    await refreshAdmin();
    return;
  }
  const enableButton = event.target.closest("[data-enable-user]");
  if (enableButton) {
    await fetchJson(`/api/users/${enableButton.dataset.enableUser}/enable`, { method: "POST" });
    await refreshAdmin();
  }
});

document.querySelector("#upload-toast-close").addEventListener("click", () => {
  document.querySelector("#upload-toast").hidden = true;
  window.clearTimeout(state.toastTimer);
});

if (window.location.hash === "#contacts") {
  openContactsPanel().catch((error) => console.error(error));
}

loadVersion().catch((error) => {
  console.error(error);
});

initializeAuth().catch((error) => {
  console.error(error);
});
