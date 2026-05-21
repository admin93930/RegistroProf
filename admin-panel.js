/* Dashboard admin – dati da API Flask (cookie di sessione) */
'use strict';

const API_BASE = '';
const ADMIN_NAME = typeof window.__ADMIN_USER__ === 'string' ? window.__ADMIN_USER__ : 'Admin';

async function fetchJSON(url, opts) {
    const o = opts || {};
    const init = {
        credentials: 'same-origin',
        method: o.method || 'GET',
        headers: o.headers || (o.body ? { 'Content-Type': 'application/json' } : {}),
        body: o.body !== undefined ? (typeof o.body === 'string' ? o.body : JSON.stringify(o.body)) : undefined
    };
    const r = await fetch(API_BASE + url, init);
    const j = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, data: j };
}

function mapVotoDaApi(v) {
    return {
        id: v.id,
        utente: v.user,
        prof: v.nomeProf || v.nome_professore || '',
        materia: v.materia || '',
        scuola: v.scuola || '',
        anno: '-',
        voto: parseInt(String(v.voto), 10) || 0,
        data: (v.timestamp || '').split(' ')[0] || '-'
    };
}

function mapRecensioneDaApi(r) {
    return {
        id: r.id,
        utente: r.user,
        prof: r.nomeProfRec || '',
        testo: r.recensione || '',
        votoRel: '-',
        data: (r.timestamp || '').split(' ')[0] || '-'
    };
}

function mediaPerProfilo(nomeProf, listaVotiApi) {
    const votiNumerici = listaVotiApi
        .filter(v => (v.nomeProf || '').trim().toLowerCase() === (nomeProf || '').trim().toLowerCase())
        .map(v => parseFloat(String(v.voto).replace(',', '.')))
        .filter(x => !isNaN(x));
    if (!votiNumerici.length) return '-';
    return (votiNumerici.reduce((a, b) => a + b, 0) / votiNumerici.length).toFixed(1);
}

let editIndex = null;
let ticketAdminCorrenteId = null;
let charts = {};
let datiCorrenti = { professori: [], voti: [], recensioni: [], utenti: [], log: [], avvisi: [], segnalazioni: [], rawVoti: [] };
let darkMode = localStorage.getItem('darkMode') === 'true';
let votiSelezionati = [];
let editProfId = null;
let credUserId = null;

function mostraToast(messaggio, tipo = 'success') {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = messaggio;
    toast.className = tipo + ' show';
    setTimeout(() => (toast.className = ''), 3000);
}

function toggleDarkMode() {
    darkMode = !darkMode;
    document.body.classList.toggle('dark-mode', darkMode);
    localStorage.setItem('darkMode', darkMode);
    const lb = document.getElementById('darkModeLabel');
    if (lb) lb.textContent = darkMode ? 'Disattiva Dark Mode' : 'Attiva Dark Mode';
    const th = document.querySelector('.theme-toggle');
    if (th) th.innerHTML = darkMode ? '☀️' : '🌙';
    aggiornaChartsTheme();
}

function aggiornaChartsTheme() {
    const textColor = darkMode ? '#eee' : '#333';
    const gridColor = darkMode ? '#0f3460' : '#e0e0e0';
    Object.values(charts).forEach(chart => {
        if (chart?.options?.plugins?.legend?.labels)
            chart.options.plugins.legend.labels.color = textColor;
        if (chart?.options?.scales?.x?.ticks)
            chart.options.scales.x.ticks.color = textColor;
        if (chart?.options?.scales?.y?.ticks)
            chart.options.scales.y.ticks.color = textColor;
        if (chart?.options?.scales?.x?.grid)
            chart.options.scales.x.grid.color = gridColor;
        if (chart?.options?.scales?.y?.grid)
            chart.options.scales.y.grid.color = gridColor;
        chart.update?.();
    });
}

function initCharts(placeholders) {
    const textColor = darkMode ? '#eee' : '#333';
    const gridColor = darkMode ? '#0f3460' : '#e0e0e0';
    const commonOptions = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: textColor, font: { size: 11 } } } },
        scales: {
            x: { ticks: { color: textColor, font: { size: 10 } }, grid: { color: gridColor } },
            y: { beginAtZero: true, ticks: { color: textColor, font: { size: 10 } }, grid: { color: gridColor } }
        }
    };
    const ph = placeholders || {
        linea: { labels: [], values: [] },
        scuole: { labels: ['Nessun dato'], values: [1] },
        materie: { labels: [], values: [] },
        ruoli: { labels: ['Studenti', 'Admin'], values: [1, 0] }
    };

    if (charts.voti) charts.voti.destroy();
    charts.voti = new Chart(document.getElementById('chartVoti'), {
        type: 'line',
        data: {
            labels: ph.linea.labels,
            datasets: [{
                label: 'Voti inseriti',
                data: ph.linea.values,
                borderColor: '#667eea',
                backgroundColor: 'rgba(102,126,234,0.1)',
                tension: 0.4,
                fill: true
            }]
        },
        options: commonOptions
    });

    if (charts.scuole) charts.scuole.destroy();
    charts.scuole = new Chart(document.getElementById('chartScuole'), {
        type: 'doughnut',
        data: {
            labels: ph.scuole.labels,
            datasets: [{ data: ph.scuole.values, backgroundColor: ['#667eea', '#764ba2', '#4CAF50', '#FF9800', '#2196F3', '#9C27B0', '#00BCD4', '#795548'] }]
        },
        options: { ...commonOptions, plugins: { ...commonOptions.plugins, legend: { position: 'bottom', labels: { color: textColor, font: { size: 10 } } } } }
    });

    if (charts.materie) charts.materie.destroy();
    charts.materie = new Chart(document.getElementById('chartMaterie'), {
        type: 'bar',
        data: {
            labels: ph.materie.labels.length ? ph.materie.labels : ['N/D'],
            datasets: [{ label: 'Media voto', data: ph.materie.values.length ? ph.materie.values : [0], backgroundColor: '#667eea' }]
        },
        options: { ...commonOptions, plugins: { ...commonOptions.plugins, legend: { display: false } } }
    });

    if (charts.ruoli) charts.ruoli.destroy();
    charts.ruoli = new Chart(document.getElementById('chartRuoli'), {
        type: 'pie',
        data: {
            labels: ph.ruoli.labels,
            datasets: [{ data: ph.ruoli.values, backgroundColor: ['#4CAF50', '#f44336'] }]
        },
        options: { ...commonOptions, plugins: { ...commonOptions.plugins, legend: { position: 'bottom', labels: { color: textColor, font: { size: 10 } } } } }
    });
}

function applicaDatiGrafici(ch) {
    if (!ch || !charts.voti) return;
    charts.voti.data.labels = ch.linea_voti.labels;
    charts.voti.data.datasets[0].data = ch.linea_voti.values;
    charts.voti.update();

    const sl = ch.doughnut_scuole.labels.length ? ch.doughnut_scuole : { labels: ['Nessuno'], values: [1] };
    charts.scuole.data.labels = sl.labels;
    charts.scuole.data.datasets[0].data = sl.values;
    charts.scuole.update();

    charts.materie.data.labels = ch.bar_materie.labels.length ? ch.bar_materie.labels : ['N/D'];
    charts.materie.data.datasets[0].data = ch.bar_materie.values.length ? ch.bar_materie.values : [0];
    charts.materie.update();

    charts.ruoli.data.labels = ch.pie_ruoli.labels;
    charts.ruoli.data.datasets[0].data = ch.pie_ruoli.values;
    charts.ruoli.update();

    const tp = ch.top_prof;
    if (tp && tp.nome) {
        document.getElementById('topProfNome').textContent = tp.nome;
        document.getElementById('topProfMedia').textContent = (tp.media != null ? tp.media : '-') + '/10';
        document.getElementById('topProfVoti').textContent = String(tp.n_voti ?? '-');
        document.getElementById('topProfRec').textContent = String(tp.n_recensioni ?? '-');
    }
}

function apriSection(id, link) {
    document.querySelectorAll('section').forEach(s => s.classList.remove('active'));
    const sec = document.getElementById(id);
    if (sec) sec.classList.add('active');
    document.querySelectorAll('.sidebar a').forEach(a => a.classList.remove('active'));
    if (link) link.classList.add('active');

    const loaders = {
        voti: caricaVoti,
        recensioni: caricaRecensioni,
        professori: caricaProfessori,
        materie: caricaMaterie,
        scuole: caricaScuole,
        utenti: caricaUtenti,
        ruoli: caricaRuoli,
        registrazioni: caricaRegistrazioni,
        ticket: caricaTicketAdmin,
        sessioni: caricaSessioni,
        segnalazioni: caricaSegnalazioni,
        avvisi: caricaAvvisiAdmin,
        global_banner: caricaBannerGlobale,
        ip_ban: caricaIpBan,
        newsletter: () => {},
        ticket_templates: caricaTemplateTicket,
        privacy_rgpd: caricaPrivacyAdmin,
        danger_reset: () => {},
        log: caricaLog,
        dashboard: async () => {
            await Promise.all([
                caricaProfessori(false),
                caricaVoti(false),
                caricaRecensioni(false),
                caricaUtenti(false)
            ]);
            aggiornaStats();
            const ch = await fetchJSON('/api/analytics/charts?t=' + Date.now());
            if (ch.ok) applicaDatiGrafici(ch.data);
            else initCharts();
        },
        impostazioni: loadImpostazioniCard
    };
    if (loaders[id]) loaders[id]();
}

function aggiornaStats() {
    document.getElementById('statProfessori').textContent = datiCorrenti.professori?.length || 0;
    document.getElementById('statVoti').textContent = datiCorrenti.voti?.length || 0;
    document.getElementById('statRecensioni').textContent = datiCorrenti.recensioni?.length || 0;
    document.getElementById('statUtenti').textContent = datiCorrenti.utenti?.length || 0;
}

async function caricaUtenti(andToast = true) {
    const r = await fetchJSON('/api/utenti?t=' + Date.now());
    if (!r.ok) return mostraToast(r.data.error || 'Errore utenti', 'error');
    datiCorrenti.utenti = r.data || [];
    renderUtenti();
    aggiornaStats();
    if (andToast !== false && andToast !== true); // no toast default
}

function renderUtenti() {
    const tbody = document.querySelector('#tabellaUtenti tbody');
    if (!tbody) return;
    tbody.innerHTML = datiCorrenti.utenti.map(u => `
        <tr>
            <td>${u.username}</td>
            <td><span class="badge ${u.role === 'admin' ? 'badge-admin' : 'badge-user'}">${u.role}</span></td>
            <td><span class="badge badge-attivo">${u.account_status || 'attivo'}</span></td>
            <td>${u.created_at}</td>
            <td>${u.voti_count}</td>
            <td>${u.recensioni_count}</td>
            <td>
                <button type="button" class="btn btn-info btn-sm" onclick="schedaUtente(${u.id})">📋</button>
                <button type="button" class="btn btn-warning btn-sm" onclick="apriCredenziali(${u.id}, ${JSON.stringify(u.username)})">✏️</button>
                <button type="button" class="btn btn-primary btn-sm" onclick="modificaUtentePrompt(${u.id})">🛠️</button>
                <button type="button" class="btn btn-danger btn-sm" onclick="eliminaUtenteAdmin(${u.id})">🗑️</button>
            </td>
        </tr>
    `).join('');
}

function filtraUtenti() {
    const q = (document.querySelector('#utenti .filter-input')?.value || '').toLowerCase();
    document.querySelectorAll('#tabellaUtenti tbody tr').forEach(row => {
        row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
}

async function schedaUtente(userId) {
    const res = await fetchJSON('/api/utenti-anagrafica/' + userId);
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    const a = res.data;
    document.getElementById('anagraficaContent').innerHTML = `
        <p><strong>Utente:</strong> ${escapeHtml(a.username)}</p>
        <p><strong>Email:</strong> ${escapeHtml(a.email || '-')}</p>
        <p><strong>Nome:</strong> ${escapeHtml(a.nome_cognome || '-')}</p>
        <p><strong>Scuola:</strong> ${escapeHtml(a.scuola || '-')}</p>
        <p><strong>Registrato:</strong> ${escapeHtml(a.created_at || '-')}</p>
        <p><strong>Ultimo login:</strong> ${escapeHtml(a.last_login || '-')}</p>
        <p><strong>Voti / Recensioni:</strong> ${a.voti_count} / ${a.recensioni_count}</p>
    `;
    document.getElementById('modalAnagrafica').classList.add('active');
}
function chiudiModalAnagrafica() {
    document.getElementById('modalAnagrafica').classList.remove('active');
}

async function apriModalUtente() {
    const sel = document.getElementById('nuovoUtRuolo');
    if (sel) {
        const r = await fetchJSON('/api/ruoli?t=' + Date.now());
        if (r.ok && Array.isArray(r.data)) {
            sel.innerHTML = r.data.map(x => `<option value="${escapeHtml(x.nome)}">${escapeHtml(x.nome)}</option>`).join('');
            if (!sel.value) sel.value = 'user';
        }
    }
    document.getElementById('modalNuovoUtente')?.classList.add('active');
}
function chiudiModalNuovoUtente() {
    document.getElementById('modalNuovoUtente')?.classList.remove('active');
}

async function creaNuovoUtenteDashboard() {
    const u = document.getElementById('nuovoUtUsername')?.value?.trim();
    const p = document.getElementById('nuovoUtPassword')?.value;
    const role = document.getElementById('nuovoUtRuolo')?.value || 'user';
    if (!u || !p) return mostraToast('Username e password obbligatori', 'error');
    const res = await fetchJSON('/api/utenti/crea', { method: 'POST', body: { username: u, password: p, role } });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    chiudiModalNuovoUtente();
    mostraToast('Utente creato');
    await caricaUtenti();
}

async function gestioneRuoli() {
    const msg = prompt('Username da promuovere ad admin + INVIO,\noppure formato: nomeutente,user (per retrocedere):');
    if (!msg) return;
    const parts = msg.split(',').map(s => s.trim());
    const nm = parts[0];
    const role = parts[1] === 'admin' || parts[1] === 'user' ? parts[1] : 'admin';
    const u = datiCorrenti.utenti.find(x => x.username === nm);
    if (!u) return mostraToast('Utente non in elenco — aggiorna', 'error');
    const res = await fetchJSON('/api/utenti/' + u.id, {
        method: 'PUT',
        body: { role }
    });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    mostraToast('Ruolo aggiornato');
    await caricaUtenti();
}

function apriCredenziali(userId, username) {
    credUserId = userId;
    document.getElementById('credUsernameCorrente').textContent = username.replace(/[<>&]/g, '');
    document.getElementById('formNuovoUsername').value = '';
    document.getElementById('formNuovaPassword').value = '';
    document.getElementById('modalCredenziali').classList.add('active');
}

function chiudiModalCredenziali() {
    document.getElementById('modalCredenziali').classList.remove('active');
    credUserId = null;
}

async function salvaCredenzialiUtente() {
    const body = {};
    const nuUser = document.getElementById('formNuovoUsername').value.trim();
    const nuPass = document.getElementById('formNuovaPassword').value;
    if (nuUser) body.username = nuUser;
    if (nuPass) body.password = nuPass;
    if (!Object.keys(body).length) return chiudiModalCredenziali();
    const res = await fetchJSON('/api/utenti/' + credUserId + '/credenziali', {
        method: 'PUT',
        body
    });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    chiudiModalCredenziali();
    mostraToast('Credenziali aggiornate');
    await caricaUtenti();
}

async function modificaUtentePrompt(uid) {
    const target = datiCorrenti.utenti.find(u => u.id === uid);
    if (!target) return;
    const role = prompt('Nuovo ruolo utente (es: user/admin/moderatore):', target.role || 'user');
    if (role === null) return;
    const status = prompt('Nuovo stato account (attivo/sospeso/bannato):', target.account_status || 'attivo');
    if (status === null) return;
    const res = await fetchJSON('/api/utenti-anagrafica/' + uid, { method: 'PUT', body: { role: role.trim(), account_status: status.trim() } });
    if (!res.ok) return mostraToast(res.data.error || 'Errore modifica utente', 'error');
    mostraToast('Utente aggiornato');
    await caricaUtenti();
}

async function eliminaUtenteAdmin(uid) {
    if (!confirm('Eliminare questo utente?')) return;
    const res = await fetchJSON('/api/utenti/' + uid, { method: 'DELETE' });
    if (!res.ok) return mostraToast(res.data.error || 'Errore eliminazione', 'error');
    await caricaUtenti();
}

async function caricaVoti(andRender = true) {
    const r = await fetchJSON('/api/voti?t=' + Date.now());
    if (!r.ok) return mostraToast(r.data.error || 'Errore voti', 'error');
    datiCorrenti.rawVoti = r.data;
    datiCorrenti.voti = (r.data || []).map(mapVotoDaApi);
    aggiornaStats();
    if (andRender !== false) renderVoti();
}

function renderVoti() {
    const tbody = document.querySelector('#tabellaVoti tbody');
    if (!tbody) return;

    let filtrati = datiCorrenti.voti.slice();
    const filtroTesto = document.getElementById('filtroTestoVoti')?.value.toLowerCase() || '';
    const filtroMateria = document.getElementById('filtroMateria')?.value || '';
    const filtroScuola = document.getElementById('filtroScuola')?.value || '';

    filtrati = filtrati.filter(v => {
        return (!filtroTesto || JSON.stringify(v).toLowerCase().includes(filtroTesto)) &&
            (!filtroMateria || v.materia === filtroMateria) &&
            (!filtroScuola || v.scuola === filtroScuola);
    });

    tbody.innerHTML = '';
    filtrati.forEach(v => {
        const row = tbody.insertRow();
        row.innerHTML = `
            <td><input type="checkbox" onchange="toggleVoto(${v.id}, this)" ${votiSelezionati.includes(v.id) ? 'checked' : ''}></td>
            <td>${escapeHtml(v.utente)}</td>
            <td>${escapeHtml(v.prof)}</td>
            <td>${escapeHtml(v.materia)}</td>
            <td>${escapeHtml(v.scuola)}</td>
            <td>${escapeHtml(v.anno)}</td>
            <td><strong style="color:${getVotoColor(v.voto)}">${v.voto}/10</strong></td>
            <td>${escapeHtml(v.data)}</td>
            <td>
                <button type="button" class="btn btn-warning btn-sm" onclick="apriModalVoto(${v.id})">✏️</button>
                <button type="button" class="btn btn-danger btn-sm" onclick="eliminaVotoAdmin(${v.id})">🗑️</button>
            </td>`;
    });
    const cv = document.getElementById('countVoti');
    if (cv) cv.textContent = filtrati.length;
    calcolaMediaVoti(filtrati);
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c]));
}

function getVotoColor(voto) {
    if (voto >= 9) return '#4CAF50';
    if (voto >= 7) return '#2196F3';
    if (voto >= 6) return '#FF9800';
    return '#f44336';
}

function calcolaMediaVoti(voti) {
    const el = document.getElementById('mediaVoti');
    if (!el) return;
    if (!voti || voti.length === 0) {
        el.textContent = '-';
        return;
    }
    el.textContent = (voti.reduce((s, v) => s + v.voto, 0) / voti.length).toFixed(2) + '/10';
}

function filtraVoti() {
    renderVoti();
}

function resetFiltriVoti() {
    if (document.getElementById('filtroTestoVoti')) document.getElementById('filtroTestoVoti').value = '';
    if (document.getElementById('filtroMateria')) document.getElementById('filtroMateria').value = '';
    if (document.getElementById('filtroScuola')) document.getElementById('filtroScuola').value = '';
    if (document.getElementById('filtroAnno')) document.getElementById('filtroAnno').value = '';
    renderVoti();
}

function toggleSelectAllVoti(checkbox) {
    const filtroRows = [...document.querySelectorAll('#tabellaVoti tbody tr')]; // dopo render non abbiamo id facili
    votiSelezionati = checkbox.checked ? datiCorrenti.voti.map(v => v.id) : [];
    renderVoti();
}

function toggleVoto(id, checkbox) {
    if (checkbox.checked) votiSelezionati.push(id);
    else votiSelezionati = votiSelezionati.filter(x => x !== id);
}

async function eliminaVotiSelezionati() {
    if (!votiSelezionati.length) return mostraToast('Nessuno selezionato', 'error');
    if (!confirm('Eliminare ' + votiSelezionati.length + ' voti?')) return;
    for (const id of votiSelezionati)
        await fetchJSON('/api/voti/' + id, { method: 'DELETE' });
    votiSelezionati = [];
    await caricaVoti();
}

async function eliminaVotoAdmin(id) {
    if (!confirm('Eliminare questo voto?')) return;
    const res = await fetchJSON('/api/voti/' + id, { method: 'DELETE' });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    await caricaVoti();
}

function aggiungiVoto() {
    apriModalVoto(null);
}

async function apriModalVoto(id) {
    editIndex = id;
    document.getElementById('titoloModalVoto').textContent = id ? 'Modifica Voto' : 'Aggiungi Voto';
    const ur = await fetchJSON('/api/utenti?t=' + Date.now());
    const usuari = ur.ok ? ur.data : [];
    const selU = document.getElementById('formVotoUtente');
    selU.innerHTML = '<option value="">Utente...</option>' + usuari.map(u => `<option value="${escapeHtml(u.username)}">${escapeHtml(u.username)}</option>`).join('');

    const profs = datiCorrenti.professori || [];
    const selP = document.getElementById('formVotoProf');
    selP.innerHTML = '<option value="">Prof... (campo libero)</option>';
    document.getElementById('formVotoMateria').value = '';
    document.getElementById('formVotoScuola').value = '';
    document.getElementById('formVotoNote').value = '';

    profs.slice(0, 100).forEach(p => {
        const o = document.createElement('option');
        o.value = p.nome;
        o.textContent = `${p.nome} — ${p.materia || ''}`;
        selP.appendChild(o);
    });

    selP.onchange = () => {
        const pname = selP.value;
        const pf = datiCorrenti.professori.find(p => p.nome === pname);
        if (pf) {
            document.getElementById('formVotoMateria').value = pf.materia || '';
            document.getElementById('formVotoScuola').value = pf.scuola || '';
        }
    };

    const annoSel = document.getElementById('formVotoAnno');
    if (annoSel) {
        const now = new Date();
        const y = now.getMonth() >= 7 ? now.getFullYear() : now.getFullYear() - 1;
        annoSel.innerHTML = '';
        for (let i = 0; i < 14; i++) {
            const a = y - i;
            const val = `${a}/${a + 1}`;
            const o = document.createElement('option');
            o.value = val; o.textContent = val;
            annoSel.appendChild(o);
        }
    }

    if (id) {
        const v = datiCorrenti.voti.find(x => x.id === id);
        if (v) {
            selU.value = v.utente;
            document.getElementById('formVotoProf').value = '';
            document.getElementById('formVotoMateria').value = v.materia;
            document.getElementById('formVotoScuola').value = v.scuola;
            document.getElementById('formVotoValore').value = v.voto;
            const inpFree = document.getElementById('formNomeProfLibero');
            if (inpFree) inpFree.value = v.prof;
        }
    } else {
        document.getElementById('formVotoValore').value = '';
        const inpFree = document.getElementById('formNomeProfLibero');
        if (inpFree) inpFree.value = '';
    }
    document.getElementById('modalVoto').classList.add('active');
}

function chiudiModalVoto() {
    document.getElementById('modalVoto').classList.remove('active');
    editIndex = null;
}

async function salvaVoto() {
    const utente = document.getElementById('formVotoUtente').value;
    const profCampoLibero = document.getElementById('formNomeProfLibero');
    let nomeProf =
        document.getElementById('formVotoProf').value ||
        (profCampoLibero ? profCampoLibero.value.trim() : '');
    const materia = document.getElementById('formVotoMateria').value.trim();
    const scuola = document.getElementById('formVotoScuola').value.trim();
    const votoVal = parseInt(document.getElementById('formVotoValore').value, 10);
    if (!utente || !nomeProf || !materia || votoVal < 1 || votoVal > 10) {
        mostraToast('Compila tutti i campi (voto 1–10)', 'error');
        return;
    }
    let res;
    if (editIndex) {
        res = await fetchJSON('/api/voti/' + editIndex, {
            method: 'PUT',
            body: { nomeProf, materia, scuola, voto: String(votoVal) }
        });
    } else {
        res = await fetchJSON('/api/voti', {
            method: 'POST',
            body: { username: utente, nomeProf, materia, scuola, voto: String(votoVal) }
        });
    }
    if (!res.ok) return mostraToast(res.data.error || 'Errore salvataggio', 'error');
    chiudiModalVoto();
    await caricaVoti();
    mostraToast('Salvato');
}

function exportExcel(tipo) {
    let data = [], filename = '';
    if (tipo === 'voti') {
        data = datiCorrenti.voti.map(v => ({
            Utente: v.utente,
            Professore: v.prof,
            Materia: v.materia,
            Scuola: v.scuola,
            Voto: v.voto,
            Data: v.data
        }));
        filename = 'voti_export.xlsx';
    } else if (tipo === 'recensioni') {
        data = datiCorrenti.recensioni.map(r => ({
            Utente: r.utente,
            Professore: r.prof,
            Recensione: r.testo
        }));
        filename = 'recensioni_export.xlsx';
    } else if (tipo === 'professori') {
        data = datiCorrenti.professori.map(p => ({
            Nome: p.nome,
            Materia: p.materia,
            Scuola: p.scuola
        }));
        filename = 'professori_export.xlsx';
    } else if (tipo === 'log') {
        data = datiCorrenti.log.map(l => ({
            Data: l.data,
            Tipo: l.tipo,
            Azione: l.azione,
            Utente: l.utente
        }));
        filename = 'log_export.xlsx';
    }
    if (!data.length) return mostraToast('Nessun dato da esportare', 'error');
    const ws = XLSX.utils.json_to_sheet(data);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Dati');
    XLSX.writeFile(wb, filename);
}

function exportPDF(tipo) {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    doc.setFontSize(16);
    doc.text(`Export ${tipo} – Registro`, 14, 20);
    let columns = [],
        rows = [];
    if (tipo === 'voti') {
        columns = ['Utente', 'Prof', 'Mat', 'Scuola', 'V', 'Data'];
        rows = datiCorrenti.voti.map(v => [
            v.utente,
            v.prof,
            v.materia,
            v.scuola,
            String(v.voto),
            v.data
        ]);
    }
    doc.autoTable({ head: [columns], body: rows, startY: 28 });
    doc.save(tipo + '_export.pdf');
    mostraToast('PDF salvato');
}

function stampaVoti() {
    window.print();
}

async function caricaRecensioni(doRender = true) {
    const r = await fetchJSON('/api/recensioni?t=' + Date.now());
    if (!r.ok) return;
    datiCorrenti.recensioni = (r.data || []).map(mapRecensioneDaApi);
    aggiornaStats();
    if (doRender !== false) renderRecensioni();
}

function renderRecensioni() {
    const tbody = document.querySelector('#tabellaRecensioni tbody');
    if (!tbody) return;
    tbody.innerHTML = datiCorrenti.recensioni
        .map(
            rw => `
        <tr>
            <td>${escapeHtml(rw.utente)}</td>
            <td>${escapeHtml(rw.prof)}</td>
            <td>${escapeHtml((rw.testo || '').substring(0, 120))}${(rw.testo || '').length > 120 ? '…' : ''}</td>
            <td>-</td>
            <td>${escapeHtml(rw.data)}</td>
            <td>
              <button type="button" class="btn btn-warning btn-sm" onclick="modificaRecensioneAdminPrompt(${rw.id})">✏️</button>
              <button type="button" class="btn btn-danger btn-sm" onclick="eliminaRecensioneAdmin(${rw.id})">🗑️</button>
            </td>
        </tr>`
        )
        .join('');
}

function filtraRecensioni() {
    const inp = document.querySelector('#recensioni .filter-input')?.value?.toLowerCase() || '';
    document.querySelectorAll('#tabellaRecensioni tbody tr').forEach(row => {
        row.style.display = row.textContent.toLowerCase().includes(inp) ? '' : 'none';
    });
}

async function eliminaRecensioneAdmin(id) {
    if (!confirm('Eliminare recensione?')) return;
    const res = await fetchJSON('/api/recensioni/' + id, { method: 'DELETE' });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    await caricaRecensioni();
}

async function modificaRecensioneAdminPrompt(id) {
    const rec = datiCorrenti.recensioni.find(x => x.id === id);
    if (!rec) return;
    const testo = prompt('Modifica testo recensione:', rec.testo || '');
    if (testo === null) return;
    const prof = prompt('Professore:', rec.prof || '');
    if (prof === null) return;
    const res = await fetchJSON('/api/recensioni/' + id, { method: 'PUT', body: { recensione: testo, nomeProfRec: prof } });
    if (!res.ok) return mostraToast(res.data.error || 'Errore modifica recensione', 'error');
    await caricaRecensioni();
}

async function caricaProfessori(doRender = true) {
    const pr = await fetchJSON('/api/professori?t=' + Date.now());
    const vv = await fetchJSON('/api/voti?t=' + Date.now());
    const listaVoti = vv.ok ? vv.data || [] : [];
    datiCorrenti.rawVoti = listaVoti;
    const raw = pr.ok ? pr.data || [] : [];
    if (!pr.ok) return mostraToast(pr.data?.error || 'Errore professori', 'error');
    datiCorrenti.professori = raw.map(p => ({
        ...p,
        media_calc: mediaPerProfilo(p.nome, listaVoti),
        n_rec_calc: '-'
    }));
    aggiornaStats();
    if (doRender !== false) renderProfessori();
}

function renderProfessori() {
    const tbody = document.querySelector('#tabellaProfessori tbody');
    if (!tbody) return;
    tbody.innerHTML = datiCorrenti.professori
        .map(
            p => `
      <tr>
        <td>${escapeHtml(p.nome)}</td>
        <td>${escapeHtml(p.materia || '')}</td>
        <td>${escapeHtml(p.scuola || '')}</td>
        <td>${p.media_calc}</td>
        <td>-</td>
        <td>
          <button type="button" class="btn btn-warning btn-sm" onclick="modificaProfessore(${p.id})">✏️</button>
          <button type="button" class="btn btn-danger btn-sm" onclick="eliminaProfessore(${p.id})">🗑️</button>
        </td>
      </tr>`
        )
        .join('');
}

function filtraProfessori() {
    const q = (
        document.querySelector('#professori .filter-input')?.value || ''
    ).toLowerCase();
    document.querySelectorAll('#tabellaProfessori tbody tr').forEach(row => {
        row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
}

async function eliminaProfessore(id) {
    if (!confirm('Elimina professore?')) return;
    const res = await fetchJSON('/api/professori/' + id, { method: 'DELETE' });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    await caricaProfessori();
}

function modificaProfessore(id) {
    const p = datiCorrenti.professori.find(x => x.id === id);
    if (!p) return;
    editProfId = id;
    document.getElementById('formProfNome').value = p.nome;
    document.getElementById('formProfMateria').value = p.materia || '';
    document.getElementById('formProfScuola').value = p.scuola || '';
    document.getElementById('formProfDescrizione').value = p.descrizione || '';
    document.getElementById('titoloModalProf').textContent = 'Modifica professore';
    document.getElementById('modalProfessoreWrap').classList.add('active');
}

function chiudiProfModal() {
    document.getElementById('modalProfessoreWrap').classList.remove('active');
    editProfId = null;
}

async function salvaProfessoreDashboard() {
    const nome = document.getElementById('formProfNome').value.trim();
    const materia = document.getElementById('formProfMateria').value.trim();
    const scuola = document.getElementById('formProfScuola').value.trim();
    const descrizione = document.getElementById('formProfDescrizione').value.trim();
    if (!nome) return mostraToast('Nome richiesto', 'error');
    let res;
    if (editProfId)
        res = await fetchJSON('/api/professori/' + editProfId, {
            method: 'PUT',
            body: { nome, materia, scuola, descrizione }
        });
    else
        res = await fetchJSON('/api/professori', {
            method: 'POST',
            body: { nome, materia, scuola, descrizione }
        });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    chiudiProfModal();
    await caricaProfessori();
}

function apriModalProf() {
    editProfId = null;
    document.getElementById('titoloModalProf').textContent = 'Nuovo professore';
    document.getElementById('formProfNome').value = '';
    document.getElementById('formProfMateria').value = '';
    document.getElementById('formProfScuola').value = '';
    document.getElementById('formProfDescrizione').value = '';
    document.getElementById('modalProfessoreWrap').classList.add('active');
}

async function caricaRegistrazioni() {
    const r = await fetchJSON('/api/registrazioni');
    if (!r.ok) return;
    const tbody = document.querySelector('#tabellaRegistrazioni tbody');
    const lista = Array.isArray(r.data) ? r.data : [];
    const pending = lista.filter(x => x.stato === 'in_attesa').length;
    const badge = document.getElementById('badgeRegistrazioni');
    const count = document.getElementById('countRegistrazioni');
    if (badge) badge.style.display = pending ? 'inline-block' : 'none';
    if (count) {
        count.style.display = pending ? 'inline-block' : 'none';
        count.textContent = pending + ' in attesa';
    }
    tbody.innerHTML = lista
        .map(
            reg => `
      <tr>
        <td>#${reg.id}</td>
        <td>${escapeHtml(reg.username)}</td>
        <td>${escapeHtml(reg.email || '')}</td>
        <td>${escapeHtml(reg.nome_cognome || '')}</td>
        <td>${escapeHtml(reg.scuola || '')}</td>
        <td>${reg.data_registrazione || ''}</td>
        <td><span class="badge badge-pending">${reg.stato}</span></td>
        <td>
          <button type="button" class="btn btn-success btn-sm" onclick="approvaRegistrazione(${reg.id})" ${reg.stato !== 'in_attesa' ? 'disabled' : ''}>✅</button>
          <button type="button" class="btn btn-danger btn-sm" onclick="rifiutaRegistrazione(${reg.id})" ${reg.stato !== 'in_attesa' ? 'disabled' : ''}>❌</button>
        </td>
      </tr>`
        )
        .join('');
}

async function approvaRegistrazione(id) {
    const res = await fetchJSON('/api/registrazioni/' + id, {
        method: 'PUT',
        body: { stato: 'approvato' }
    });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    mostraToast('Approvata');
    await caricaRegistrazioni();
}

async function rifiutaRegistrazione(id) {
    const res = await fetchJSON('/api/registrazioni/' + id, {
        method: 'PUT',
        body: { stato: 'rifiutato' }
    });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    mostraToast('Rifiutata');
    await caricaRegistrazioni();
}

async function approvaTutte() {
    const r = await fetchJSON('/api/registrazioni');
    if (!r.ok) return;
    const pending = (r.data || []).filter(x => x.stato === 'in_attesa');
    for (const p of pending) await approvaRegistrazione(p.id);
}

let ticketsCache = [];

async function caricaTicketAdmin() {
    const r = await fetchJSON('/api/ticket?t=' + Date.now());
    if (!r.ok) return;
    ticketsCache = r.data || [];
    const open = ticketsCache.filter(t => t.stato === 'aperto' || t.stato === 'in_lavorazione').length;
    const b = document.getElementById('badgeTicket');
    const c = document.getElementById('countTicketAperti');
    if (b) {
        b.style.display = open ? 'inline-block' : 'none';
        b.textContent = open;
    }
    if (c) {
        c.style.display = open ? 'inline-block' : 'none';
        c.textContent = open + ' aperti';
    }
    renderTicketTable();
}

function filtraTicket() {
    renderTicketTable();
}

function renderTicketTable() {
    const filtro = document.querySelector('#ticket .filter-select')?.value || '';
    const tbody = document.querySelector('#tabellaTicketAdmin tbody');
    if (!tbody) return;
    let rows = ticketsCache.slice();
    if (filtro) rows = rows.filter(t => t.stato === filtro);
    tbody.innerHTML = rows
        .map(
            t => `
      <tr id="ticket-${t.id}">
        <td>#${t.id}</td>
        <td>${escapeHtml(t.utente)}</td>
        <td>${escapeHtml(t.oggetto)}</td>
        <td>${escapeHtml(t.priorita)}</td>
        <td>${escapeHtml(t.stato)}</td>
        <td>${t.data_apertura || ''}</td>
        <td><button type="button" class="btn btn-primary btn-sm" onclick="vediTicketAdmin(${t.id})">👁️</button></td>
      </tr>`
        )
        .join('');
}

async function vediTicketAdmin(id) {
    ticketAdminCorrenteId = id;
    const r = await fetchJSON('/api/ticket/' + id);
    if (!r.ok) return mostraToast('Errore caricamento', 'error');
    const t = r.data;
    document.getElementById('ticketAdminTitolo').textContent = 'Ticket #' + id;
    document.getElementById('ticketAdminInfo').innerHTML = `
        <p><strong>Utente:</strong> ${escapeHtml(t.utente)}</p>
        <p><strong>Oggetto:</strong> ${escapeHtml(t.oggetto)}</p>
        <p><strong>Messaggio:</strong> ${escapeHtml(t.messaggio)}</p>
        <p><strong>Stato:</strong> ${escapeHtml(t.stato)}</p>`;
    const box = document.getElementById('ticketAdminRisposte');
    box.innerHTML = (t.risposte || [])
        .map(
            x => `<div class="risposta-item" style="margin:8px 0;padding:10px;background:#fff;border-radius:8px;">
            <small>${escapeHtml(x.data || '')} — ${escapeHtml(x.da || '')}</small><p>${escapeHtml(x.messaggio || '')}</p></div>`
        )
        .join('');
    document.getElementById('ticketAdminRisposta').value = '';
    document.getElementById('ticketAdminStato').value = t.stato || 'aperto';
    document.getElementById('modalTicketAdmin').classList.add('active');
}

function chiudiTicketAdmin() {
    document.getElementById('modalTicketAdmin').classList.remove('active');
}

function usaTemplate(testo) {
    const el = document.getElementById('ticketAdminRisposta');
    if (el) el.value = testo;
    mostraToast('Template inserito', 'info');
}

function inviaPushNotification() {
    apriPushModal();
}

async function rispondiTicketAdmin() {
    const risposta = document.getElementById('ticketAdminRisposta').value.trim();
    const stato = document.getElementById('ticketAdminStato').value;
    const body = {};
    if (risposta) body.risposta = risposta;
    if (stato) body.stato = stato;
    if (!Object.keys(body).length) return chiudiTicketAdmin();
    const res = await fetchJSON('/api/ticket/' + ticketAdminCorrenteId, {
        method: 'PUT',
        body
    });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    chiudiTicketAdmin();
    await caricaTicketAdmin();
    mostraToast('Ticket aggiornato');
}

async function caricaSessioni() {
    const r = await fetchJSON('/api/sessioni');
    if (!r.ok) return;
    const tbody = document.querySelector('#tabellaSessioni tbody');
    tbody.innerHTML = (r.data || [])
        .map(
            s => `
      <tr>
        <td>${escapeHtml(s.username)}</td>
        <td>${escapeHtml(s.ip || '')}</td>
        <td>${s.login_time || ''}</td>
        <td>${s.last_activity || ''}</td>
        <td>${escapeHtml((s.user_agent || '').substring(0, 60))}</td>
        <td><button type="button" class="btn btn-danger btn-sm" onclick="terminaSessione(${JSON.stringify(s.session_id)})" ${s.username === ADMIN_NAME ? 'disabled' : ''}>🚫</button></td>
      </tr>`
        )
        .join('');
}

async function terminaSessione(sid) {
    if (!confirm('Terminare sessione?')) return;
    const res = await fetchJSON('/api/sessioni/' + encodeURIComponent(sid), { method: 'DELETE' });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    await caricaSessioni();
}

async function terminaTutteSessioni() {
    if (!confirm('Terminare tutte le sessioni tranne la tua?')) return;
    const r = await fetchJSON('/api/sessioni');
    if (!r.ok) return;
    for (const s of r.data || []) {
        if (s.username !== ADMIN_NAME)
            await fetchJSON('/api/sessioni/' + encodeURIComponent(s.session_id), { method: 'DELETE' });
    }
    await caricaSessioni();
}

async function caricaSegnalazioni() {
    const r = await fetchJSON('/api/segnalazioni');
    if (!r.ok) return;
    const lista = Array.isArray(r.data) ? r.data : [];
    datiCorrenti.segnalazioni = lista;
    const pend = lista.filter(x => x.stato === 'pending').length;
    const b = document.getElementById('badgeSegnalazioni');
    const c = document.getElementById('countSegnalazioni');
    if (b) {
        b.style.display = pend ? 'inline-block' : 'none';
        b.textContent = pend;
    }
    if (c) {
        c.style.display = pend ? 'inline-block' : 'none';
        c.textContent = pend + ' nuove';
    }
    document.querySelector('#tabellaSegnalazioni tbody').innerHTML = lista
        .map(
            s => `
      <tr>
        <td>#${s.id}</td>
        <td>${escapeHtml(s.tipo)}</td>
        <td>${escapeHtml(s.segnalatore)}</td>
        <td>${escapeHtml((s.motivo || '').substring(0, 80))}</td>
        <td>${s.data || ''}</td>
        <td>${escapeHtml(s.stato)}</td>
        <td>
          <button type="button" class="btn btn-success btn-sm" onclick="gestisciSegnalazione(${s.id},'resolved')">✔</button>
          <button type="button" class="btn btn-warning btn-sm" onclick="gestisciSegnalazione(${s.id},'dismissed')">✗</button>
        </td>
      </tr>`
        )
        .join('');
}

async function gestisciSegnalazione(id, stato) {
    const note = prompt('Nota admin (opzionale):') || '';
    const res = await fetchJSON('/api/segnalazioni/' + id, {
        method: 'PUT',
        body: { stato, admin_note: note }
    });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    await caricaSegnalazioni();
}

async function caricaAvvisiAdmin() {
    const r = await fetchJSON('/api/admin/avvisi');
    if (!r.ok) return;
    datiCorrenti.avvisi = r.data || [];
    renderAvvisi();
}

async function caricaBannerGlobale() {
    const r = await fetchJSON('/api/admin/banner');
    if (!r.ok) return mostraToast(r.data.error || 'Errore banner', 'error');
    const b = r.data || {};
    const msg = document.getElementById('bannerMessaggio');
    const att = document.getElementById('bannerAttivo');
    const info = document.getElementById('bannerInfo');
    if (msg) msg.value = b.messaggio || '';
    if (att) att.checked = !!b.attivo;
    if (info) info.textContent = 'Ultimo aggiornamento: ' + (b.aggiornato || '-');
}

async function salvaBannerGlobale() {
    const messaggio = document.getElementById('bannerMessaggio')?.value?.trim() || '';
    const attivo = !!document.getElementById('bannerAttivo')?.checked;
    const r = await fetchJSON('/api/admin/banner', { method: 'PUT', body: { messaggio, attivo } });
    if (!r.ok) return mostraToast(r.data.error || 'Errore salvataggio banner', 'error');
    mostraToast('Banner aggiornato');
    await caricaBannerGlobale();
}

async function caricaIpBan() {
    const r = await fetchJSON('/api/admin/banned-ip');
    if (!r.ok) return mostraToast(r.data.error || 'Errore IP ban', 'error');
    const tbody = document.querySelector('#tabellaIpBan tbody');
    if (!tbody) return;
    tbody.innerHTML = (r.data || []).map(x => `
      <tr>
        <td>${x.id}</td>
        <td>${escapeHtml(x.ip || '')}</td>
        <td>${escapeHtml(x.motivo || '')}</td>
        <td>${escapeHtml(x.creato_il || '')}</td>
        <td>${escapeHtml(x.banned_by || '')}</td>
        <td><button class="btn btn-danger btn-sm" onclick="rimuoviIpBan(${x.id})">Rimuovi</button></td>
      </tr>`).join('');
}

async function aggiungiIpBan() {
    const ip = document.getElementById('ipBanInput')?.value?.trim();
    const motivo = document.getElementById('ipBanMotivo')?.value?.trim() || '';
    if (!ip) return mostraToast('Inserisci IP', 'error');
    const r = await fetchJSON('/api/admin/banned-ip', { method: 'POST', body: { ip, motivo } });
    if (!r.ok) return mostraToast(r.data.error || 'Errore blocco IP', 'error');
    document.getElementById('ipBanInput').value = '';
    document.getElementById('ipBanMotivo').value = '';
    mostraToast('IP bloccato');
    await caricaIpBan();
}

async function rimuoviIpBan(id) {
    if (!confirm('Rimuovere questo IP dalla blacklist?')) return;
    const r = await fetchJSON('/api/admin/banned-ip/' + id, { method: 'DELETE' });
    if (!r.ok) return mostraToast(r.data.error || 'Errore rimozione', 'error');
    await caricaIpBan();
}

async function inviaNewsletter() {
    const subject = document.getElementById('newsSubject')?.value?.trim() || '';
    const html = document.getElementById('newsHtml')?.value?.trim() || '';
    const anche_admin = !!document.getElementById('newsAncheAdmin')?.checked;
    if (!subject || !html) return mostraToast('Oggetto e corpo obbligatori', 'error');
    const r = await fetchJSON('/api/admin/newsletter', { method: 'POST', body: { subject, html, anche_admin } });
    if (!r.ok) return mostraToast(r.data.error || 'Errore invio newsletter', 'error');
    mostraToast(`Newsletter inviata: ${r.data.inviati || 0}, scartate: ${r.data.saltati || 0}`);
}

async function caricaTemplateTicket() {
    const r = await fetchJSON('/api/admin/ticket-templates');
    if (!r.ok) return mostraToast(r.data.error || 'Errore template', 'error');
    const tbody = document.querySelector('#tabellaTicketTemplates tbody');
    if (!tbody) return;
    tbody.innerHTML = (r.data || []).map(t => `
      <tr>
        <td>${t.id}</td>
        <td>${escapeHtml(t.nome || '')}</td>
        <td>${escapeHtml(t.oggetto || '')}</td>
        <td>${escapeHtml(t.tipo || '')}</td>
        <td>${escapeHtml((t.corpo || '').slice(0, 140))}${(t.corpo || '').length > 140 ? '…' : ''}</td>
        <td><button class="btn btn-danger btn-sm" onclick="eliminaTemplateTicket(${t.id})">Elimina</button></td>
      </tr>`).join('');
}

async function creaTemplateTicket() {
    const nome = document.getElementById('tplNome')?.value?.trim() || '';
    const oggetto = document.getElementById('tplOggetto')?.value?.trim() || '';
    const tipo = document.getElementById('tplTipo')?.value?.trim() || 'generale';
    const corpo = document.getElementById('tplCorpo')?.value?.trim() || '';
    if (!nome || !corpo) return mostraToast('Nome e corpo obbligatori', 'error');
    const r = await fetchJSON('/api/admin/ticket-templates', { method: 'POST', body: { nome, oggetto, tipo, corpo } });
    if (!r.ok) return mostraToast(r.data.error || 'Errore creazione template', 'error');
    document.getElementById('tplNome').value = '';
    document.getElementById('tplOggetto').value = '';
    document.getElementById('tplTipo').value = '';
    document.getElementById('tplCorpo').value = '';
    await caricaTemplateTicket();
}

async function eliminaTemplateTicket(id) {
    if (!confirm('Eliminare template?')) return;
    const r = await fetchJSON('/api/admin/ticket-templates/' + id, { method: 'DELETE' });
    if (!r.ok) return mostraToast(r.data.error || 'Errore eliminazione', 'error');
    await caricaTemplateTicket();
}

// ===== Materie / Scuole / Ruoli =====
async function caricaMaterie() {
    const r = await fetchJSON('/api/materie?t=' + Date.now());
    if (!r.ok) return mostraToast(r.data.error || 'Errore materie', 'error');
    const tb = document.querySelector('#tabellaMaterie tbody');
    if (!tb) return;
    tb.innerHTML = (r.data || []).map(m => `<tr><td>${m.id}</td><td>${escapeHtml(m.nome || '')}</td>
      <td><button class="btn btn-warning btn-sm" onclick="modificaMateria(${m.id}, decodeURIComponent('${encodeURIComponent(m.nome || '')}'))">✏️</button>
      <button class="btn btn-danger btn-sm" onclick="eliminaMateria(${m.id})">🗑️</button></td></tr>`).join('');
}
async function creaMateria() {
    const nome = document.getElementById('materiaNome')?.value?.trim();
    if (!nome) return mostraToast('Nome materia richiesto', 'error');
    const r = await fetchJSON('/api/materie', { method: 'POST', body: { nome } });
    if (!r.ok) return mostraToast(r.data.error || 'Errore creazione materia', 'error');
    document.getElementById('materiaNome').value = '';
    await caricaMaterie();
}
async function modificaMateria(id, oldName) {
    const nome = prompt('Nuovo nome materia:', oldName || '');
    if (nome === null) return;
    const r = await fetchJSON('/api/materie/' + id, { method: 'PUT', body: { nome } });
    if (!r.ok) return mostraToast(r.data.error || 'Errore modifica materia', 'error');
    await caricaMaterie();
}
async function eliminaMateria(id) {
    if (!confirm('Eliminare questa materia?')) return;
    const r = await fetchJSON('/api/materie/' + id, { method: 'DELETE' });
    if (!r.ok) return mostraToast(r.data.error || 'Errore eliminazione materia', 'error');
    await caricaMaterie();
}

async function caricaScuole() {
    const r = await fetchJSON('/api/scuole?t=' + Date.now());
    if (!r.ok) return mostraToast(r.data.error || 'Errore scuole', 'error');
    const tb = document.querySelector('#tabellaScuole tbody');
    if (!tb) return;
    tb.innerHTML = (r.data || []).map(s => `<tr><td>${s.id}</td><td>${escapeHtml(s.nome || '')}</td>
      <td><button class="btn btn-warning btn-sm" onclick="modificaScuola(${s.id}, decodeURIComponent('${encodeURIComponent(s.nome || '')}'))">✏️</button>
      <button class="btn btn-danger btn-sm" onclick="eliminaScuola(${s.id})">🗑️</button></td></tr>`).join('');
}
async function creaScuola() {
    const nome = document.getElementById('scuolaNome')?.value?.trim();
    if (!nome) return mostraToast('Nome scuola richiesto', 'error');
    const r = await fetchJSON('/api/scuole', { method: 'POST', body: { nome } });
    if (!r.ok) return mostraToast(r.data.error || 'Errore creazione scuola', 'error');
    document.getElementById('scuolaNome').value = '';
    await caricaScuole();
}
async function modificaScuola(id, oldName) {
    const nome = prompt('Nuovo nome scuola:', oldName || '');
    if (nome === null) return;
    const r = await fetchJSON('/api/scuole/' + id, { method: 'PUT', body: { nome } });
    if (!r.ok) return mostraToast(r.data.error || 'Errore modifica scuola', 'error');
    await caricaScuole();
}
async function eliminaScuola(id) {
    if (!confirm('Eliminare questa scuola?')) return;
    const r = await fetchJSON('/api/scuole/' + id, { method: 'DELETE' });
    if (!r.ok) return mostraToast(r.data.error || 'Errore eliminazione scuola', 'error');
    await caricaScuole();
}

async function caricaRuoli() {
    const r = await fetchJSON('/api/ruoli?t=' + Date.now());
    if (!r.ok) return mostraToast(r.data.error || 'Errore ruoli', 'error');
    const tb = document.querySelector('#tabellaRuoli tbody');
    if (!tb) return;
    tb.innerHTML = (r.data || []).map(x => `<tr><td>${x.id}</td><td>${escapeHtml(x.nome)}</td><td>${x.is_system ? 'Sì' : 'No'}</td>
      <td>${x.is_system ? '-' : `<button class="btn btn-warning btn-sm" onclick="modificaRuolo(${x.id}, decodeURIComponent('${encodeURIComponent(x.nome || '')}'))">✏️</button><button class="btn btn-danger btn-sm" onclick="eliminaRuolo(${x.id})">🗑️</button>`}</td></tr>`).join('');
}
async function creaRuolo() {
    const nome = document.getElementById('ruoloNome')?.value?.trim();
    if (!nome) return mostraToast('Nome ruolo richiesto', 'error');
    const r = await fetchJSON('/api/ruoli', { method: 'POST', body: { nome } });
    if (!r.ok) return mostraToast(r.data.error || 'Errore creazione ruolo', 'error');
    document.getElementById('ruoloNome').value = '';
    await caricaRuoli(); await caricaUtenti();
}
async function modificaRuolo(id, oldName) {
    const nome = prompt('Nuovo nome ruolo:', oldName || '');
    if (nome === null) return;
    const r = await fetchJSON('/api/ruoli/' + id, { method: 'PUT', body: { nome } });
    if (!r.ok) return mostraToast(r.data.error || 'Errore modifica ruolo', 'error');
    await caricaRuoli(); await caricaUtenti();
}
async function eliminaRuolo(id) {
    if (!confirm('Eliminare ruolo?')) return;
    const r = await fetchJSON('/api/ruoli/' + id, { method: 'DELETE' });
    if (!r.ok) return mostraToast(r.data.error || 'Errore eliminazione ruolo', 'error');
    await caricaRuoli(); await caricaUtenti();
}

// ===== Creazioni per conto utente =====
function apriModalRecensioneAdmin() { document.getElementById('modalRecensioneAdmin')?.classList.add('active'); }
function chiudiModalRecensioneAdmin() { document.getElementById('modalRecensioneAdmin')?.classList.remove('active'); }
async function creaRecensioneAdmin() {
    const username = document.getElementById('recAdminUsername').value.trim();
    const nomeProfRec = document.getElementById('recAdminProf').value.trim();
    const scuola = document.getElementById('recAdminScuola').value.trim();
    const recensione = document.getElementById('recAdminTesto').value.trim();
    const is_anonymous = !!document.getElementById('recAdminAnon').checked;
    const r = await fetchJSON('/api/recensioni', { method: 'POST', body: { username, nomeProfRec, scuola, recensione, is_anonymous } });
    if (!r.ok) return mostraToast(r.data.error || 'Errore creazione recensione', 'error');
    chiudiModalRecensioneAdmin(); await caricaRecensioni();
}
function apriModalRegistrazioneAdmin() { document.getElementById('modalRegistrazioneAdmin')?.classList.add('active'); }
function chiudiModalRegistrazioneAdmin() { document.getElementById('modalRegistrazioneAdmin')?.classList.remove('active'); }
async function creaRegistrazioneAdmin() {
    const body = {
        username: document.getElementById('regAdminUsername').value.trim(),
        password: document.getElementById('regAdminPassword').value,
        email: document.getElementById('regAdminEmail').value.trim(),
        nome_cognome: document.getElementById('regAdminNome').value.trim(),
        scuola: document.getElementById('regAdminScuola').value.trim()
    };
    const r = await fetchJSON('/api/registrazioni', { method: 'POST', body });
    if (!r.ok) return mostraToast(r.data.error || 'Errore creazione registrazione', 'error');
    chiudiModalRegistrazioneAdmin(); await caricaRegistrazioni();
}
function apriModalTicketAdminCreate() { document.getElementById('modalTicketAdminCreate')?.classList.add('active'); }
function chiudiModalTicketAdminCreate() { document.getElementById('modalTicketAdminCreate')?.classList.remove('active'); }
async function creaTicketAdminPerUtente() {
    const body = {
        utente: document.getElementById('tktAdminUser').value.trim(),
        oggetto: document.getElementById('tktAdminOggetto').value.trim(),
        messaggio: document.getElementById('tktAdminMsg').value.trim(),
        priorita: document.getElementById('tktAdminPriorita').value
    };
    const r = await fetchJSON('/api/ticket', { method: 'POST', body });
    if (!r.ok) return mostraToast(r.data.error || 'Errore creazione ticket', 'error');
    chiudiModalTicketAdminCreate(); await caricaTicketAdmin();
}
function apriModalSegnalazioneAdmin() { document.getElementById('modalSegnalazioneAdmin')?.classList.add('active'); }
function chiudiModalSegnalazioneAdmin() { document.getElementById('modalSegnalazioneAdmin')?.classList.remove('active'); }
async function creaSegnalazioneAdmin() {
    const body = {
        segnalatore: document.getElementById('segAdminSegnalatore').value.trim(),
        tipo: document.getElementById('segAdminTipo').value.trim(),
        indice: parseInt(document.getElementById('segAdminIndice').value, 10) || null,
        motivo: document.getElementById('segAdminMotivo').value.trim()
    };
    const r = await fetchJSON('/api/segnalazioni', { method: 'POST', body });
    if (!r.ok) return mostraToast(r.data.error || 'Errore creazione segnalazione', 'error');
    chiudiModalSegnalazioneAdmin(); await caricaSegnalazioni();
}
function apriModalPrivacyAdmin() { document.getElementById('modalPrivacyAdminCreate')?.classList.add('active'); }
function chiudiModalPrivacyAdmin() { document.getElementById('modalPrivacyAdminCreate')?.classList.remove('active'); }
async function creaRichiestaPrivacyAdmin() {
    const body = { username: document.getElementById('privacyAdminUser').value.trim(), motivo: document.getElementById('privacyAdminMotivo').value.trim() };
    const r = await fetchJSON('/api/privacy/delete-requests', { method: 'POST', body });
    if (!r.ok) return mostraToast(r.data.error || 'Errore richiesta privacy', 'error');
    chiudiModalPrivacyAdmin(); await caricaPrivacyAdmin();
}

async function eseguiResetSistema() {
    if (!confirm('Operazione PERICOLOSA: confermi reset sistema?')) return;
    const password = document.getElementById('dangerResetPwd')?.value || '';
    const confirmText = document.getElementById('dangerResetConfirm')?.value || '';
    const r = await fetchJSON('/api/admin/system-reset', { method: 'POST', body: { password, confirm: confirmText } });
    if (!r.ok) return mostraToast(r.data.error || 'Reset fallito', 'error');
    mostraToast('Reset completato');
}

function renderAvvisi() {
    const tbody = document.querySelector('#tabellaAvvisi tbody');
    if (!tbody) return;
    tbody.innerHTML = datiCorrenti.avvisi
        .map(
            a => `
      <tr>
        <td>#${a.id}</td>
        <td>${escapeHtml(a.titolo)}</td>
        <td>${a.attivo ? 'Sì' : 'No'}</td>
        <td>${escapeHtml(a.priority || '')}</td>
        <td>${escapeHtml(a.created_at || '')}</td>
        <td>${escapeHtml(a.expires_at || '-')}</td>
        <td>
          <button type="button" class="btn btn-warning btn-sm" onclick="toggleAvviso(${a.id}, ${!a.attivo})">${a.attivo ? 'Nascondi' : 'Pubblica'}</button>
          <button type="button" class="btn btn-danger btn-sm" onclick="eliminaAvviso(${a.id})">Elimina</button>
        </td>
      </tr>`
        )
        .join('');
}

async function pubblicaNuovoAvviso() {
    const titolo = document.getElementById('avvisoTitolo')?.value?.trim();
    const contenuto = document.getElementById('avvisoContenuto')?.value?.trim();
    const priority = document.getElementById('avvisoPriorita')?.value || 'normal';
    const expires_at = document.getElementById('avvisoScadenza')?.value || '';
    if (!titolo || !contenuto) return mostraToast('Titolo e testo richiesti', 'error');
    const res = await fetchJSON('/api/admin/avvisi', {
        method: 'POST',
        body: { titolo, contenuto, attivo: true, priority, expires_at: expires_at || null }
    });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    document.getElementById('avvisoTitolo').value = '';
    document.getElementById('avvisoContenuto').value = '';
    if (document.getElementById('avvisoScadenza')) document.getElementById('avvisoScadenza').value = '';
    await caricaAvvisiAdmin();
    mostraToast('Avviso pubblicato');
}

async function toggleAvviso(id, attivo) {
    await fetchJSON('/api/admin/avvisi/' + id, { method: 'PUT', body: { attivo } });
    await caricaAvvisiAdmin();
}

async function eliminaAvviso(id) {
    if (!confirm('Elimina avviso?')) return;
    await fetchJSON('/api/admin/avvisi/' + id, { method: 'DELETE' });
    await caricaAvvisiAdmin();
}

async function caricaPrivacyAdmin() {
    const r = await fetchJSON('/api/privacy/delete-requests');
    if (!r.ok) return;
    const tbody = document.querySelector('#tabellaPrivacy tbody');
    tbody.innerHTML = (r.data || [])
        .map(
            req => `
      <tr>
        <td>#${req.id}</td>
        <td>${escapeHtml(req.username)}</td>
        <td>${escapeHtml((req.motivo || '').substring(0, 60))}</td>
        <td>${escapeHtml(req.stato)}</td>
        <td>${req.data_richiesta || ''}</td>
        <td>
          <button type="button" class="btn btn-success btn-sm" onclick="privacyDecision(${req.id},'approved')">Elimina dati utente</button>
          <button type="button" class="btn btn-danger btn-sm" onclick="privacyDecision(${req.id},'rejected')">Rifiuta</button>
        </td>
      </tr>`
        )
        .join('');
}

async function privacyDecision(id, stato) {
    const note = prompt('Nota (opzionale):') || '';
    const res = await fetchJSON('/api/privacy/delete-requests/' + id, {
        method: 'PUT',
        body: { stato, admin_note: note }
    });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    mostraToast(res.data.message || 'Aggiornato');
    await caricaPrivacyAdmin();
}

async function caricaLog() {
    const r = await fetchJSON('/api/admin/audit-log?limit=300');
    if (!r.ok) return;
    datiCorrenti.log = (r.data || []).map(row => ({
        id: row.id,
        data: row.timestamp,
        tipo: row.esito === 'ko' ? 'sistema' : row.attore === ADMIN_NAME ? 'admin' : 'user',
        azione: row.azione + ' — ' + (row.target || ''),
        utente: row.attore
    }));
    renderLog();
}

function renderLog() {
    const container = document.getElementById('listaLog');
    const filtro = document.querySelector('#log .filter-select')?.value || '';
    const logsFiltrati = datiCorrenti.log.filter(l => !filtro || l.tipo === filtro);
    container.innerHTML = logsFiltrati
        .map(
            l => `
        <div class="log-entry ${l.tipo}">
            <strong>${l.tipo === 'admin' ? '👑' : l.tipo === 'user' ? '👤' : '⚙️'} ${escapeHtml(l.azione)}</strong>
            <span style="color:#666;"> • ${escapeHtml(l.utente)}</span>
            <span class="time">${escapeHtml(l.data)}</span>
        </div>`
        )
        .join('');
}

function filtraLog() {
    renderLog();
}

function toggleNotifiche() {
    const d = document.getElementById('dropdownNotifiche');
    d.classList.toggle('aperto');
    if (d.classList.contains('aperto')) caricaNotificheAdmin();
}

async function caricaNotificheAdmin() {
    const r = await fetchJSON('/api/notifiche?t=' + Date.now());
    if (!r.ok) return;
    const notifiche = Array.isArray(r.data) ? r.data : [];
    const nonLetto = notifiche.filter(n => !n.letta).length;
    const badge = document.getElementById('badgeNotifiche');
    badge.textContent = nonLetto > 99 ? '99+' : String(nonLetto);
    badge.classList.toggle('nascosto', nonLetto === 0);
    const lista = document.getElementById('listaNotifiche');
    lista.innerHTML = notifiche.length
        ? notifiche
              .map(
                  n => `
          <div class="notifica-item ${n.letta ? 'letta' : 'nuova'}" data-nid="${n.id}">
              <span class="notifica-tipo ${n.tipo}">${escapeHtml(n.tipo)}</span>
              <div class="notifica-titolo">${escapeHtml(n.titolo)}</div>
              <div class="notifica-messaggio">${escapeHtml(n.messaggio)}</div>
              <small>${escapeHtml(n.data)}</small>
          </div>`
              )
              .join('')
        : '<div class="nessuna-notifica">Nessuna notifica</div>';
    lista.querySelectorAll('.notifica-item').forEach(el => {
        el.onclick = () => marcaNotificaLetta(el.dataset.nid);
    });
}

async function marcaNotificaLetta(id) {
    await fetchJSON('/api/notifiche/' + id, {
        method: 'PUT'
    });
    caricaNotificheAdmin();
}

async function segnaTutteLette() {
    await fetchJSON('/api/notifiche/segna-tutte-lette', { method: 'POST' });
    caricaNotificheAdmin();
    mostraToast('Segnate tutte come lette');
}

function chiudiPushModal() {
    document.getElementById('modalPush').classList.remove('active');
}

function apriPushModal() {
    document.getElementById('modalPush').classList.add('active');
}

async function inviaPush() {
    const titolo = document.getElementById('pushTitolo').value.trim();
    const messaggio = document.getElementById('pushMessaggio').value.trim();
    const tipo = document.getElementById('pushTipo').value;
    const destSel = document.getElementById('pushDestinatari').value;
    const map = {
        tutti: 'all',
        studenti: 'users',
        professori: 'users',
        admin: 'admins'
    };
    const filtro = map[destSel] || 'users';
    if (!titolo || !messaggio) return mostraToast('Titolo e messaggio richiesti', 'error');
    const res = await fetchJSON('/api/admin/notifiche-broadcast', {
        method: 'POST',
        body: {
            titolo,
            messaggio,
            tipo,
            filtro
        }
    });
    if (!res.ok) return mostraToast(res.data.error || 'Errore', 'error');
    chiudiPushModal();
    document.getElementById('lastPush').textContent = new Date().toLocaleString('it-IT');
    mostraToast('Inviate a ' + (res.data.inviate || '') + ' utenti');
}

function loadImpostazioniCard() {
    document.getElementById('lastBackup').textContent =
        localStorage.getItem('lastBackup') || '-';
}

function eseguiBackup() {
    mostraToast('Per backup reale usa export dal database SQLite in produzione', 'info');
    const data = new Date().toLocaleString('it-IT');
    localStorage.setItem('lastBackup', data);
    document.getElementById('lastBackup').textContent = data;
}

function pulisciCache() {
    mostraToast('Cache locale vuota — forzato aggiornamento dati…', 'info');
    prefetchAll();
}

function ottimizzaDatabase() {
    mostraToast(
        'Suggerimento: esegui VACUUM sul file SQLite dall’host',
        'info'
    );
}

function toggleChat() {
    document.getElementById('chatWidget').classList.toggle('active');
}

function inviaChat() {
    const input = document.getElementById('chatInput');
    const testo = input.value.trim();
    if (!testo) return;
    document.getElementById('chatBody').innerHTML += `<div class="chat-message admin">${escapeHtml(testo)}</div>`;
    input.value = '';
    mostraToast('Usa i ticket assistenza per comunicazioni salvate sul server', 'info');
}

document.addEventListener('click', e => {
    const container = document.querySelector('.notifiche-container');
    const dropdown = document.getElementById('dropdownNotifiche');
    if (dropdown && container && !container.contains(e.target))
        dropdown.classList.remove('aperto');
});

async function prefetchAll() {
    try {
        const ov = await fetchJSON('/api/analytics/overview');
        await caricaVoti(false);
        await caricaProfessori(false);
        await caricaRecensioni(false);
        await caricaUtenti(false);
        await caricaTicketAdmin();
        await caricaSegnalazioni();

        aggiornaStats();

        const chRes = await fetchJSON('/api/analytics/charts?t=' + Date.now());
        if (!charts.voti) initCharts();
        if (chRes.ok) applicaDatiGrafici(chRes.data);

        await caricaNotificheAdmin();
        const reg = await fetchJSON('/api/registrazioni');
        if (reg.ok) {
            const p = reg.data.filter(x => x.stato === 'in_attesa').length;
            document.getElementById('badgeRegistrazioni').style.display =
                p ? 'inline-block' : 'none';
        }
        mostraToast('Benvenuto, ' + ADMIN_NAME + '!', 'success');
    } catch (e) {
        console.error(e);
        mostraToast('Errore avvio dashboard', 'error');
    }
}

window.addEventListener('load', () => {
    if (darkMode) {
        document.body.classList.add('dark-mode');
        const lb = document.getElementById('darkModeLabel');
        if (lb) lb.textContent = 'Disattiva Dark Mode';
        const th = document.querySelector('.theme-toggle');
        if (th) th.innerHTML = '☀️';
    }
    document.getElementById('dashHeaderUser').textContent = ADMIN_NAME;
    initCharts();
    prefetchAll();

    document.querySelectorAll('.modal').forEach(modal =>
        modal.addEventListener('click', e => {
            if (e.target === modal) modal.classList.remove('active');
        })
    );

    window.addEventListener('hashchange', () => {
        const h = window.location.hash.replace(/^#/, '');
        if (!h.startsWith('ticket')) return;
        const m = /^ticket-(\d+)$/.exec(h);
        if (m) vediTicketAdmin(parseInt(m[1], 10));
    });

    const hInit = window.location.hash || '';
    if (hInit.startsWith('#ticket-')) {
        const m = /^#ticket-(\d+)$/.exec(hInit);
        if (m) setTimeout(() => vediTicketAdmin(parseInt(m[1], 10)), 500);
    }
});
