"""Constatimmo portal scraper: login, roadmap extraction, detail enrichment."""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from loguru import logger
from playwright.async_api import Page

from calendar_sync.config import Settings
from calendar_sync.models.appointment import Appointment, AppointmentSource
from calendar_sync.scrapers.base import BrowserManager
from calendar_sync.utils.helpers import compact_text, extract_odm_number


def get_constatimmo_planning_url(settings: Settings) -> str:
    """Build the Constatimmo planning URL."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(settings.constatimmo_url)
        return f"{parsed.scheme}://{parsed.netloc}/profile#planification"
    except Exception:
        return "https://constatonline.constatimmo.com/profile#planification"


def get_constatimmo_detail_url(odm_number: str) -> Optional[str]:
    """Build Constatimmo detail URL from ODM number."""
    clean = re.search(r"\d{6,}", odm_number)
    if not clean:
        return None
    return f"https://v2.constatimmo.com/index.php?odmRedirect={clean.group()}"


async def dump_constatimmo_debug(page: Page, label: str) -> None:
    """Save debug screenshots and HTML."""
    from pathlib import Path

    debug_dir = Path("debug")
    debug_dir.mkdir(exist_ok=True)

    import datetime

    stamp = datetime.datetime.now().isoformat().replace(":", ".").replace(".", "-")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", label)
    base = debug_dir / f"constatimmo-{safe_label}-{stamp}"

    try:
        await page.screenshot(path=str(base) + ".png", full_page=True)
    except Exception:
        pass

    try:
        html = await page.content()
        (base.with_suffix(".html")).write_text(html, encoding="utf-8")
    except Exception:
        pass


async def login_constatimmo(settings: Settings) -> list[Appointment]:
    """Full Constatimmo login flow, roadmap extraction, and detail enrichment."""
    if not settings.constatimmo_url or not settings.constatimmo_username or not settings.constatimmo_password:
        logger.info("[CONSTATIMMO] Missing credentials, extraction skipped.")
        return []

    manager = BrowserManager(
        headless=settings.constatimmo_headless,
        user_data_dir=settings.constatimmo_user_data_dir,
    )

    browser = await manager.launch()

    if isinstance(browser, BrowserContext):
        page = await browser.new_page()
    else:
        page = await browser.new_page()

    try:
        logger.info(f"[CONSTATIMMO] Connecting to {settings.constatimmo_url}")
        await page.goto(settings.constatimmo_url, wait_until="networkidle")

        user_selectors = [
            "#sign_in > div:nth-child(1) > div > input",
            "#sign_in input[type='email']",
            "input[name='email']",
            "input[type='email']",
        ]
        pass_selectors = [
            "#sign_in > div:nth-child(2) > div > input",
            "#sign_in input[type='password']",
            "input[name='password']",
            "input[type='password']",
        ]
        submit_selectors = [
            "#sign_in > div:nth-child(3) > div.col-xs-4 > button",
            "#sign_in button[type='submit']",
            "button[type='submit']",
        ]

        async def fill_first_visible(selectors: list[str], value: str) -> bool:
            for sel in selectors:
                handle = await page.query_selector(sel)
                if not handle:
                    continue
                visible = await page.evaluate(
                    "(el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)",
                    handle,
                )
                if not visible:
                    continue
                await page.click(sel, click_count=3)
                await page.type(sel, value, delay=30)
                return True
            return False

        user_ok = await fill_first_visible(user_selectors, settings.constatimmo_username)
        pass_ok = await fill_first_visible(pass_selectors, settings.constatimmo_password)

        if user_ok and pass_ok:
            for sel in submit_selectors:
                try:
                    btn = await page.query_selector(sel)
                    if btn:
                        await btn.click()
                        break
                except Exception:
                    continue
            await asyncio.wait_for(
                page.wait_for_load_state("networkidle"),
                timeout=20,
            )

        planning_url = get_constatimmo_planning_url(settings)
        final_url = page.url

        for attempt in range(1, 3):
            await page.goto(planning_url, wait_until="networkidle")
            await page.wait_for_timeout(8000)
            final_url = page.url
            if not re.search(r"/sso/login", final_url):
                break

            logger.warning(f"[CONSTATIMMO] SSO redirect detected (attempt {attempt}/2): {final_url}")
            if attempt < 2:
                relog_user = await fill_first_visible(user_selectors, settings.constatimmo_username)
                relog_pass = await fill_first_visible(pass_selectors, settings.constatimmo_password)
                if relog_user and relog_pass:
                    for sel in submit_selectors:
                        try:
                            btn = await page.query_selector(sel)
                            if btn:
                                await btn.click()
                                break
                        except Exception:
                            continue
                    await asyncio.wait_for(
                        page.wait_for_load_state("networkidle"),
                        timeout=20,
                    )

        await page.wait_for_timeout(4000)
        await dump_constatimmo_debug(page, "after-planning-nav")

        # Click "Mon activite" -> "Mes disponibilites"
        await page.evaluate("""() => {
            const findByText = (txt) => {
                const nodes = Array.from(document.querySelectorAll('a,button,li,span,div'));
                return nodes.find((n) => (n.innerText || '').trim().toLowerCase() === txt);
            };
            const monActivite = findByText('mon activité');
            if (monActivite) (monActivite.closest('a,button,li,div') || monActivite).click();
            const mesDispos = findByText('mes disponibilités');
            if (mesDispos) (mesDispos.closest('a,button,li,div') || mesDispos).click();
        }""")
        await page.wait_for_timeout(4000)

        await page.evaluate("""() => {
            const cb = document.querySelector('#comingOrdersCheckbox');
            if (cb && !cb.checked) cb.click();
            if (typeof window.getComingOrders === 'function') window.getComingOrders();
        }""")
        await page.wait_for_timeout(5000)
        await dump_constatimmo_debug(page, "before-extraction")

        events = await extract_constatimmo_appointments(page)
        enriched_events = await enrich_constatimmo_appointments(page, events, settings)

        enriched_count = sum(
            1 for e in enriched_events
            if e.source == AppointmentSource.CONSTATIMMO
            and any([e.owner, e.manager, e.tenant, e.comment, e.key_pickup_place, e.key_drop_place, e.floor, e.door, e.digicode, e.building])
        )
        logger.info(f"[CONSTATIMMO] {len(enriched_events)} events detected. Enriched: {enriched_count}.")
        return enriched_events

    except Exception as e:
        logger.error(f"[CONSTATIMMO] Extraction failed: {e}")
        try:
            await page.screenshot(path="debug_constatimmo_error.png")
        except Exception:
            pass
        return []
    finally:
        await page.close()
        await manager.close()


async def extract_constatimmo_from_context(context, source_url: str) -> list[Appointment]:
    """Extract events from a page or frame context."""
    result = await context.evaluate(
        """(originUrl) => {
            const toAbs = (href) => {
                try { return new URL(href, originUrl || location.href).href; }
                catch (_) { return href || ''; }
            };
            const parseOdmNumber = (text) => ((String(text || '').match(/\\b\\d{6,}\\b/) || [])[0] || '');
            const buildDetailUrl = (odm) => odm ? `https://v2.constatimmo.com/index.php?odmRedirect=${odm}` : '';

            const roadMapRows = Array.from(document.querySelectorAll('#road-map-results table tbody tr'));
            const roadMapEvents = roadMapRows.map((row) => {
                const readCell = (title) => {
                    const cells = Array.from(row.querySelectorAll('td'));
                    const cell = cells.find((td) => ((td.getAttribute('data-title') || '').toLowerCase()).includes(title));
                    return (cell ? (cell.innerText || cell.textContent || '') : '').replace(/\\s+/g, ' ').trim();
                };
                const from = readCell('de');
                const to = readCell('a');
                const odm = readCell('odm');
                const odmNumber = parseOdmNumber(odm);
                const mission = readCell('mission');
                const metier = readCell('métier') || readCell('metier');
                const address = readCell('adresse');
                const keysStatus = readCell('clés') || readCell('cles') || readCell('clefs') || readCell('clé');
                const rowHref = row.querySelector('a[href]') ? row.querySelector('a[href]').getAttribute('href') : '';
                const detailUrl = /odmRedirect=/i.test(String(rowHref || '')) ? toAbs(rowHref) : buildDetailUrl(odmNumber);
                const propertyTypes = (odm.match(/\\(([^)]+)\\)/g) || []).map((s) => s.replace(/[()]/g, '').trim()).filter(Boolean);
                const propertyType = propertyTypes.join(' / ');
                const missionLower = mission.toLowerCase();
                const type = missionLower.includes('entrée') || missionLower.includes('entree') ? 'Entrée' : missionLower.includes('sortie') ? 'Sortie' : 'RDV';
                const cleanOdm = odmNumber || (odm.match(/\\d{6,}/g) || [odm])[0];
                const dateMatch = from.match(/\\b\\d{2}\\/\\d{2}\\/\\d{4}\\b/);
                const datePart = dateMatch ? dateMatch[0] : '';
                const fromTime = (from.match(/\\b\\d{2}:\\d{2}(?::\\d{2})?\\b/) || [''])[0].slice(0, 5);
                const toTime = (to.match(/\\b\\d{2}:\\d{2}(?::\\d{2})?\\b/) || [''])[0].slice(0, 5);
                const summaryParts = [type];
                if (cleanOdm) summaryParts.push(`ODM ${cleanOdm}`);
                if (datePart) summaryParts.push(datePart);
                if (fromTime || toTime) summaryParts.push(`${fromTime || '?'}-${toTime || '?'}`);
                const description = [
                    odm ? `ODM: ${odm}` : '',
                    propertyType ? `Type de bien: ${propertyType}` : '',
                    metier ? `Métier: ${metier}` : '',
                    mission ? `Mission: ${mission}` : '',
                    address ? `Adresse: ${address}` : '',
                    keysStatus ? `Clés: ${keysStatus}` : '',
                ].filter(Boolean).join(' | ');
                return {
                    text: summaryParts.join(' ').trim() || 'Rendez-vous Constatimmo',
                    description,
                    class: 'constatimmo-roadmap-row',
                    style: 'background-color: rgb(156, 39, 176);',
                    computedBg: 'rgb(156, 39, 176)',
                    width: 100,
                    height: 20,
                    keep: true,
                    address: address || null,
                    propertyType: propertyType || null,
                    keysStatus: keysStatus || null,
                    odmNumber: odmNumber || null,
                    detailUrl: detailUrl || null,
                    doorInfo: null,
                    caveInfo: null,
                    parkingInfo: null,
                    source: 'constatimmo',
                    pageUrl: originUrl || location.href,
                };
            }).filter((e) => e.text);

            const selectors = ['.fc-event', 'a.fc-event', '.event', '.appointment', '.planning .event', '.calendar .event', 'td div[style*="background"]', 'div[style*="background-color"]'];
            const uniq = new Set();
            const nodes = [];
            for (const sel of selectors) {
                const found = Array.from(document.querySelectorAll(sel));
                for (const el of found) { if (!uniq.has(el)) { uniq.add(el); nodes.push(el); } }
            }
            const calendarEvents = nodes.map((el) => {
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                const title = el.getAttribute('title') || '';
                const href = el.getAttribute('href') || '';
                const detailUrl = /odmRedirect=/i.test(href) ? toAbs(href) : '';
                const odmFromHref = ((detailUrl.match(/odmRedirect=(\\d{6,})/i) || [])[1] || '');
                const odmFromText = parseOdmNumber(`${text} ${title}`);
                const odmNumber = odmFromHref || odmFromText || '';
                const style = el.getAttribute('style') || '';
                const className = el.className || '';
                const rect = el.getBoundingClientRect();
                const computed = window.getComputedStyle(el);
                const computedBg = (computed.backgroundColor || '').toLowerCase();
                const visible = rect.width > 20 && rect.height > 10;
                const isPurple = computedBg.includes('156, 39, 176') || computedBg.includes('123, 31, 162') || computedBg.includes('103, 58, 183') || computedBg.includes('128, 0, 128');
                const keep = visible && (isPurple || /odm/i.test(text));
                return {
                    text: text || title || 'Rendez-vous Constatimmo',
                    description: title || text || '',
                    class: className,
                    style: style || `background-color:${computedBg};`,
                    computedBg,
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    keep,
                    odmNumber: odmNumber || null,
                    detailUrl: detailUrl || (odmNumber ? buildDetailUrl(odmNumber) : null),
                    address: null,
                    source: 'constatimmo',
                    pageUrl: originUrl || location.href,
                };
            }).filter((e) => e.keep && e.text);

            return [...roadMapEvents, ...calendarEvents];
        }""",
        source_url,
    )

    appointments = []
    for item in result:
        appointments.append(Appointment(
            text=item.get("text", ""),
            description=item.get("description"),
            css_class=item.get("class"),
            style=item.get("style"),
            computed_bg=item.get("computedBg"),
            width=item.get("width"),
            height=item.get("height"),
            keep=item.get("keep"),
            address=item.get("address"),
            property_type=item.get("propertyType"),
            keys_status=item.get("keysStatus"),
            odm_number=item.get("odmNumber"),
            detail_url=item.get("detailUrl"),
            door_info=item.get("doorInfo"),
            cave_info=item.get("caveInfo"),
            parking_info=item.get("parkingInfo"),
            source=AppointmentSource.CONSTATIMMO,
            page_url=item.get("pageUrl"),
        ))
    return appointments


async def extract_constatimmo_appointments(page: Page) -> list[Appointment]:
    """Extract events from main page + all frames, deduplicated."""
    collected = []
    collected.extend(await extract_constatimmo_from_context(page, page.url))

    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            frame_events = await extract_constatimmo_from_context(frame, frame.url)
            collected.extend(frame_events)
        except Exception:
            pass

    # Deduplicate
    seen: set[str] = set()
    dedup = []
    for evt in collected:
        key = f"{evt.text}|{evt.style}|{evt.page_url}"
        if key in seen:
            continue
        seen.add(key)
        dedup.append(evt)

    return dedup


async def extract_constatimmo_detail_fields(detail_page: Page, detail_url: str, odm_number: str) -> dict:
    """Extract detail fields from Constatimmo ODM detail page."""
    extracted = await detail_page.evaluate(
        """(meta) => {
            const normalize = (v) => String(v || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
            const bodyText = normalize(document.body ? document.body.innerText : '');
            const toKey = (s) => normalize(s).toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');

            const findByInputName = (namePatterns) => {
                const inputs = Array.from(document.querySelectorAll('input, textarea, select'));
                for (const el of inputs) {
                    const rawName = el.getAttribute('name') || el.getAttribute('id') || '';
                    const k = toKey(rawName);
                    if (!k) continue;
                    if (!namePatterns.some((p) => p.test(k))) continue;
                    const val = normalize(el.value || el.getAttribute('value') || el.textContent || '');
                    if (val) return val;
                }
                return '';
            };

            const findByLabelText = (labelPatterns) => {
                const all = Array.from(document.querySelectorAll('td, th, label, strong, b, span, div, p'));
                for (const el of all) {
                    const raw = normalize(el.textContent || '');
                    if (!raw || raw.length > 80) continue;
                    const key = toKey(raw.replace(/\\s*:\\s*$/, ''));
                    if (!labelPatterns.some((p) => p.test(key))) continue;
                    const siblings = [el.nextElementSibling, el.parentElement && el.parentElement.nextElementSibling].filter(Boolean);
                    for (const sib of siblings) {
                        const v = normalize(sib.textContent || '');
                        if (v && toKey(v) !== key) return v;
                    }
                }
                return '';
            };

            const findByRegex = (patterns) => {
                for (const p of patterns) {
                    const m = bodyText.match(p);
                    if (m && m[1]) return normalize(m[1]);
                }
                return '';
            };

            const byInputOrLabelOrRegex = (inputPatterns, labelPatterns, regexPatterns) => {
                const byInput = findByInputName(inputPatterns);
                if (byInput) return byInput;
                const byLabel = findByLabelText(labelPatterns);
                if (byLabel) return byLabel;
                return findByRegex(regexPatterns);
            };

            return {
                odmNumber: String(meta.odmNumber || ''),
                detailUrl: String(meta.detailUrl || location.href || ''),
                owner: normalize(byInputOrLabelOrRegex(
                    [/propriet/i, /owner/i],
                    [/^proprietaire$/i, /^proprietaire\\s+du\\s+bien$/i],
                    [/\\bpropri[ée]taire\\s*:\\s*([^\\n]+)/i]
                ) || findByRegex([/\\bpropri[ée]taire(?:\\s+du\\s+bien)?\\s*:\\s*([^\\n]+)/i, /\\bbailleur\\s*:\\s*([^\\n]+)/i])),
                manager: normalize(byInputOrLabelOrRegex(
                    [/gestionnaire/i, /manager/i],
                    [/^gestionnaire$/i],
                    [/\\bgestionnaire\\s*:\\s*([^\\n]+)/i]
                )),
                tenant: normalize(byInputOrLabelOrRegex(
                    [/locataire/i, /tenant/i],
                    [/^locataire$/i, /^locataire\\s+sortant$/i, /^nom\\s+locataire$/i],
                    [/\\blocataire(?:\\s+sortant)?\\s*:\\s*([^\\n]+)/i]
                ) || findByRegex([/\\binformations?\\s+occupant[\\s\\S]{0,500}?\\bnom\\s*:\\s*([^\\n]+)/i, /\\blocataire(?:\\s+sortant)?\\s*:\\s*([^\\n]+)/i])),
                tenantMobile: normalize(byInputOrLabelOrRegex(
                    [/portable/i, /telephone.*portable/i, /tel/i, /mobile/i],
                    [/^portable\\s+locataire$/i, /^tel\\.?\\s*locataire(?:\\s+sortant)?$/i],
                    [/\\b(?:portable|t[ée]l(?:[ée]phone)?)\\s+locataire(?:\\s+sortant)?\\s*:\\s*([^\\n]+)/i]
                ) || findByRegex([/\\binformations?\\s+occupant[\\s\\S]{0,600}?\\bt[ée]l[ée]phone\\s+portable\\s*:\\s*([^\\n]+)/i])),
                tenantPhone: normalize(findByRegex([
                    /\\binformations?\\s+occupant[\\s\\S]{0,600}?\\bt[ée]l[ée]phone\\s+fixe\\s*:\\s*([^\\n]+)/i,
                    /\\bt[ée]l(?:[ée]phone)?\\s+locataire(?:\\s+sortant)?\\s*:\\s*([^\\n]+)/i,
                ])),
                comment: normalize((() => {
                    const dialogTitle = Array.from(document.querySelectorAll('*')).find((el) => /zone\\s+de\\s+dialogue\\s+avec\\s+constatimmo/i.test(normalize(el.textContent || '')));
                    if (dialogTitle) {
                        const section = dialogTitle.closest('table, div') || dialogTitle.parentElement;
                        if (section) {
                            const rows = Array.from(section.querySelectorAll('tr'));
                            for (const row of rows) {
                                const cells = Array.from(row.querySelectorAll('td')).map((td) => normalize(td.innerText || td.textContent || ''));
                                if (cells.length >= 3 && cells[2]) return cells[2];
                            }
                        }
                    }
                    return findByRegex([/\\bcommentaire\\s*:\\s*([^\\n]+)/i]);
                })()),
                keyPickupPlace: normalize(byInputOrLabelOrRegex(
                    [/recup.*cle/i, /retrait.*cle/i, /pickup.*key/i],
                    [/^lieu\\s+recuperation\\s+cles$/i, /^lieu\\s+de\\s+recuperation\\s+des\\s+cles$/i, /^recuperation\\s+cles$/i],
                    [/\\blieu\\s+de\\s+r[ée]cup[ée]ration\\s+des\\s+cl[ée]s\\s*:\\s*([^\\n]+)/i, /\\br[ée]cup[ée]ration\\s+cl[ée]s\\s*:\\s*([^\\n]+)/i]
                )),
                keyDropPlace: normalize(byInputOrLabelOrRegex(
                    [/depot.*cle/i, /retour.*cle/i, /drop.*key/i],
                    [/^lieu\\s+depot\\s+cles$/i, /^lieu\\s+de\\s+depot\\s+des\\s+cles$/i, /^depot\\s+cles$/i],
                    [/\\blieu\\s+de\\s+d[ée]p[ôo]t\\s+des\\s+cl[ée]s\\s*:\\s*([^\\n]+)/i, /\\bd[ée]p[ôo]t\\s+cl[ée]s\\s*:\\s*([^\\n]+)/i]
                )),
                floor: normalize(byInputOrLabelOrRegex(
                    [/etage/i, /floor/i], [/^etage$/i], [/\\b[ée]tage\\s*:\\s*([^\\n]+)/i]
                )),
                door: normalize(byInputOrLabelOrRegex(
                    [/porte/i, /door/i], [/^porte$/i], [/\\bporte\\s*:\\s*([^\\n]+)/i]
                )),
                digicode: normalize(byInputOrLabelOrRegex(
                    [/digicode/i, /code.*porte/i], [/^digicode$/i, /^code\\s+(?:porte|immeuble)$/i],
                    [/\\bdigicode\\s*:\\s*([^\\n]+)/i, /\\bcode\\s+(?:porte|immeuble)\\s*:\\s*([^\\n]+)/i]
                )),
                building: normalize(byInputOrLabelOrRegex(
                    [/batiment/i, /immeuble/i, /building/i], [/^batiment$/i, /^immeuble$/i],
                    [/\\bb[âa]timent\\s*:\\s*([^\\n]+)/i, /\\bimmeuble\\s*:\\s*([^\\n]+)/i]
                )),
            };
        }""",
        {"detailUrl": detail_url, "odmNumber": odm_number},
    )
    return extracted


async def enrich_constatimmo_appointments(
    page: Page,
    events: list[Appointment],
    settings: Settings,
) -> list[Appointment]:
    """Enrich all Constatimmo events with detail page data."""
    if not settings.constatimmo_enrich_details:
        return events

    const_events = [e for e in events if e.source == AppointmentSource.CONSTATIMMO]
    if not const_events:
        return events

    by_odm: dict[str, dict] = {}
    for evt in const_events:
        odm_from_field = compact_text(evt.odm_number or "")
        odm_from_text = extract_odm_number(evt.text) or ""
        odm = odm_from_field or odm_from_text
        if not odm:
            continue
        if odm not in by_odm:
            by_odm[odm] = {"detailUrl": evt.detail_url or get_constatimmo_detail_url(odm)}

    if not by_odm:
        return events

    browser = page.context.browser
    if not browser:
        return events
    detail_page = await browser.new_page()
    details_by_odm: dict[str, dict] = {}

    try:
        for i, (odm, info) in enumerate(by_odm.items(), 1):
            target_url = info["detailUrl"] or get_constatimmo_detail_url(odm)
            if not target_url:
                continue
            try:
                await detail_page.goto(target_url, wait_until="networkidle", timeout=30000)
                await detail_page.wait_for_timeout(800)
                final_url = detail_page.url
                if re.search(r"/sso/login", final_url):
                    logger.warning(f"[CONSTATIMMO][DETAIL] ODM {odm}: SSO redirect, skipping.")
                    continue
                fields = await extract_constatimmo_detail_fields(detail_page, final_url, odm)
                details_by_odm[odm] = fields
                if i % 5 == 0 or i == len(by_odm):
                    logger.info(f"[CONSTATIMMO][DETAIL] {i}/{len(by_odm)} detail pages processed.")
            except Exception as e:
                logger.warning(f"[CONSTATIMMO][DETAIL] ODM {odm}: enrichment failed ({e}).")
    finally:
        await detail_page.close()

    if not details_by_odm:
        return events

    enriched = []
    for evt in events:
        if evt.source != AppointmentSource.CONSTATIMMO:
            enriched.append(evt)
            continue
        odm = compact_text(evt.odm_number or "") or extract_odm_number(evt.text) or ""
        if not odm or odm not in details_by_odm:
            enriched.append(evt)
            continue
        fields = details_by_odm[odm]
        enriched.append(evt.model_copy(update={
            "odm_number": odm,
            "detail_url": evt.detail_url or fields.get("detailUrl") or get_constatimmo_detail_url(odm),
            "owner": fields.get("owner") or evt.owner,
            "manager": fields.get("manager") or evt.manager,
            "tenant": fields.get("tenant") or evt.tenant,
            "tenant_mobile": fields.get("tenantMobile") or evt.tenant_mobile,
            "tenant_phone": fields.get("tenantPhone") or evt.tenant_phone,
            "comment": fields.get("comment") or evt.comment,
            "key_pickup_place": fields.get("keyPickupPlace") or evt.key_pickup_place,
            "key_drop_place": fields.get("keyDropPlace") or evt.key_drop_place,
            "floor": fields.get("floor") or evt.floor,
            "door": fields.get("door") or evt.door,
            "digicode": fields.get("digicode") or evt.digicode,
            "building": fields.get("building") or evt.building,
        }))

    return enriched
