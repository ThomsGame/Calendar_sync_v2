"""Snexi portal scraper: login, calendar extraction, detail enrichment."""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from loguru import logger
from playwright.async_api import Frame, Page

from calendar_sync.config import Settings
from calendar_sync.models.appointment import Appointment, AppointmentSource
from calendar_sync.scrapers.base import BrowserManager, wait_for_calendar_frame
from calendar_sync.utils.helpers import (
    add_days,
    compact_text,
    extract_os_number,
    parse_time_parts,
    parse_week_start_from_label,
    week_monday_iso,
)


async def close_cookie_popup(page: Page) -> None:
    """Dismiss Didomi cookie popup on Snexi."""
    selectors = [
        "#didomi-notice-agree-button",
        "button#didomi-notice-agree-button",
    ]

    for sel in selectors:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                logger.info("[LOGIN] Cookie consent accepted via selector.")
                await page.wait_for_timeout(800)
                return
        except Exception:
            continue

    # Fallback: text-based search
    for tag in ("button", "a"):
        try:
            clicked = await page.evaluate(f"""() => {{
                const candidates = Array.from(document.querySelectorAll('{tag}'));
                const target = candidates.find((el) => {{
                    const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                    return text.includes('accepter & fermer') || text.includes('continuer sans accepter');
                }});
                if (!target) return false;
                target.click();
                return true;
            }}""")
            if clicked:
                logger.info("[LOGIN] Cookie popup managed via text search.")
                await page.wait_for_timeout(800)
                return
        except Exception:
            continue

    logger.info("[LOGIN] No blocking cookie popup detected.")


async def click_espace_client(page: Page) -> None:
    """Click the 'Espace client' button robustly."""
    selectors = [
        'button[aria-label*="Espace client"]',
        'button[aria-label*="Ouvrir l\u2019espace client"]',
        'button[aria-label*="Ouvrir l\'espace client"]',
    ]

    for sel in selectors:
        try:
            btns = await page.query_selector_all(sel)
            for btn in btns:
                text = await btn.inner_text()
                aria = await btn.get_attribute("aria-label") or ""
                full = f"{text} {aria}".lower()
                if "espace client" in full:
                    logger.info(f"[LOGIN] Clicking Espace client via {sel}")
                    await btn.click()
                    return
        except Exception:
            continue

    # DOM fallback
    try:
        clicked = await page.evaluate("""() => {
            const nodes = Array.from(document.querySelectorAll('button,a,[role="button"],span'));
            const target = nodes.find((el) => {
                const text = `${el.innerText || ''} ${el.getAttribute('aria-label') || ''}`.toLowerCase();
                return text.includes('espace client');
            });
            if (!target) return false;
            const clickable = target.closest('button,a,[role="button"]') || target;
            clickable.click();
            return true;
        }""")
        if clicked:
            logger.info("[LOGIN] Espace client clicked via DOM fallback.")
            return
    except Exception:
        pass

    raise RuntimeError("Espace client button not found")


async def open_snexi_calendar_if_logged(page: Page) -> bool:
    """Check if session is active and open calendar directly."""
    menu_selector = "a.lien_menu[href*='experts/experts_indisponibilites.php']"
    try:
        menu = await page.query_selector(menu_selector)
        if not menu:
            return False
        visible = await page.evaluate(
            """(el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length))""",
            menu,
        )
        if not visible:
            return False
        await menu.click()
        logger.info("[LOGIN] Snexi session active, calendar opened without re-login.")
        await page.wait_for_timeout(2500)
        return True
    except Exception:
        return False


async def login_snexi(page: Page, settings: Settings) -> list[Appointment]:
    """Full Snexi login flow and calendar extraction.

    Returns list of extracted appointments.
    """
    if not settings.snexi_url:
        raise ValueError("SNEXI_URL is empty or undefined. Check your .env.")

    await page.goto(settings.snexi_url, wait_until="networkidle")
    await close_cookie_popup(page)

    # Check for active session
    if await open_snexi_calendar_if_logged(page):
        cal_frame = await wait_for_calendar_frame(page)
        if cal_frame:
            return await extract_appointments(cal_frame)

    # Click Espace client
    try:
        await click_espace_client(page)
        logger.info("[LOGIN] Espace client clicked.")
    except Exception as e:
        logger.error(f"[LOGIN] Espace client not found: {e}")
        raise

    # Find login form
    login_candidates = [
        {"user": "#login", "pass": "#password"},
        {"user": 'input[name="login"]', "pass": 'input[name="mdp"]'},
        {"user": 'input[name="username"]', "pass": 'input[name="password"]'},
        {"user": 'input[id*="login"]', "pass": 'input[id*="pass"]'},
        {"user": 'input[type="text"]', "pass": 'input[type="password"]'},
    ]

    selectors = None
    for _attempt in range(2):
        for pair in login_candidates:
            user_el = await page.query_selector(pair["user"])
            pass_el = await page.query_selector(pair["pass"])
            if not user_el or not pass_el:
                continue
            visible = await page.evaluate(
                """([u, p]) => {
                    const isVisible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                    return isVisible(u) && isVisible(p);
                }""",
                [user_el, pass_el],
            )
            if visible:
                selectors = pair
                break
        if selectors:
            break
        logger.info(f"[LOGIN] Login form not found, retrying click Espace client...")
        await click_espace_client(page)
        await page.wait_for_timeout(2000)

    if not selectors:
        # Try opening from active session
        for _ in range(8):
            if await open_snexi_calendar_if_logged(page):
                selectors = None
                break
            await page.wait_for_timeout(1500)
        else:
            await page.wait_for_selector("#login", timeout=15000)
            await page.wait_for_selector("#password", timeout=15000)
            selectors = {"user": "#login", "pass": "#password"}

    if selectors:
        logger.info("[LOGIN] Login form detected.")
        user_input = await page.query_selector(selectors["user"])
        pass_input = await page.query_selector(selectors["pass"])
        if user_input:
            await user_input.click(click_count=3)
            await user_input.type(settings.snexi_username, delay=50)
        if pass_input:
            await pass_input.click(click_count=3)
            await pass_input.type(settings.snexi_password, delay=50)

        # Wait for submit button
        await page.wait_for_function(
            """() => {
                const btns = Array.from(document.querySelectorAll('button, input[type="submit"]'));
                return btns.some((btn) => {
                    const txt = (btn.textContent || btn.value || '').trim().toLowerCase();
                    return txt.includes('connexion') || txt.includes('connecter');
                });
            }""",
            timeout=10000,
        )

        # Click submit
        buttons = await page.query_selector_all("button, input[type='submit']")
        for btn in buttons:
            text = await btn.inner_text()
            if not text:
                val = await btn.get_attribute("value") or ""
                text = val
            if "connexion" in text.lower() or "connecter" in text.lower():
                await btn.click()
                break
        else:
            raise RuntimeError("Connexion button not found in modal")

        # Wait for form to hide
        try:
            await page.wait_for_selector("#login", state="hidden", timeout=10000)
            await page.wait_for_selector("#password", state="hidden", timeout=10000)
            await page.wait_for_timeout(1000)
        except Exception:
            logger.debug("[LOGIN] Login form did not hide properly.")

    # Click Indisponibilites menu
    try:
        await page.wait_for_selector(
            "a.lien_menu[href*='experts/experts_indisponibilites.php']",
            timeout=15000,
        )
        menu = await page.query_selector(
            "a.lien_menu[href*='experts/experts_indisponibilites.php']"
        )
        if menu:
            await menu.click()
            logger.info("[LOGIN] Indisponibilites menu clicked.")
            await page.wait_for_timeout(3000)
        else:
            raise RuntimeError("Indisponibilites menu not found after login.")
    except Exception as e:
        logger.error(f"[LOGIN] {e}")
        raise

    # Wait for the iframe to appear in the DOM before polling frames
    try:
        await page.wait_for_selector(
            "iframe[src*='indisponibilites'], iframe[src*='planning'], iframe[src*='calendar'], iframe",
            timeout=15000,
        )
        logger.info("[LOGIN] iframe element detected in DOM.")
        await page.wait_for_timeout(1500)  # let it start navigating
    except Exception:
        logger.warning("[LOGIN] No iframe element found via selector — will still try frame poll.")

    # Find calendar frame
    cal_frame = await wait_for_calendar_frame(page)
    if not cal_frame:
        logger.warning("[FALLBACK] Calendar iframe not found, trying HTML fallback.")
        return []

    return await extract_appointments(cal_frame)


async def extract_appointments(cal_frame: Frame) -> list[Appointment]:
    """Extract appointments from FullCalendar iframe across 4 weeks."""
    all_appointments: list[Appointment] = []
    seen_keys: set[str] = set()
    event_selectors = ".fc-event, a.fc-event, .fc-day-grid-event, .fc-time-grid-event"

    # Wait for FullCalendar to finish rendering — retry until events appear or timeout
    for _wait_attempt in range(12):
        try:
            has_events = await cal_frame.evaluate(
                "!!document.querySelector('.fc-event, .fc-time-grid-event, .fc-day-grid-event')"
            )
            has_toolbar = await cal_frame.evaluate(
                "!!document.querySelector('.fc-toolbar, .fc-header-toolbar')"
            )
            if has_toolbar:
                if has_events:
                    logger.info("[EXTRACTION] FullCalendar ready with events.")
                    break
                else:
                    logger.debug("[EXTRACTION] FullCalendar toolbar visible but no events yet, waiting…")
        except Exception:
            pass
        await cal_frame.wait_for_timeout(1500)
    else:
        logger.warning("[EXTRACTION] FullCalendar may not have fully rendered — proceeding anyway.")

    for semaine in range(4):
        await cal_frame.wait_for_timeout(1200)

        week_data = await cal_frame.evaluate(
            """(selector) => {
                const weekLabel = (document.querySelector('.fc-toolbar h2')?.textContent || '').replace(/\\s+/g, ' ').trim();
                const headerDates = Array.from(document.querySelectorAll('th.fc-day-header[data-date], .fc-day-header[data-date]'))
                    .map((el) => el.getAttribute('data-date'))
                    .filter(Boolean);
                const nodes = Array.from(document.querySelectorAll(selector));
                const events = nodes.map((el) => {
                    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    const title = el.getAttribute('title') || '';
                    const style = el.getAttribute('style') || '';
                    const className = el.className || '';
                    const rect = el.getBoundingClientRect();
                    const dateFromParent = (el.closest('[data-date]')?.getAttribute('data-date') || '').trim() || null;
                    const timeRaw = (
                        el.querySelector('.fc-time')?.getAttribute('data-full')
                        || el.querySelector('.fc-time')?.textContent
                        || el.querySelector('.fc-event-time')?.textContent
                        || ''
                    ).replace(/\\s+/g, ' ').trim();
                    const leftMatch = style.match(/left\\s*:\\s*([\\d.]+)px/i);
                    const leftPx = leftMatch ? Number(leftMatch[1]) : Math.round(rect.left);
                    return {
                        text: text || title || 'Rendez-vous Snexi',
                        description: title || text || '',
                        class: className,
                        style,
                        date: dateFromParent,
                        timeRaw,
                        leftPx,
                        address: null,
                    };
                });
                return { weekLabel, headerDates, events };
            }""",
            event_selectors,
        )

        week_start = parse_week_start_from_label(week_data["weekLabel"]) or week_monday_iso(
            semaine
        )
        fallback_header = [add_days(week_start, i) for i in range(7) if add_days(week_start, i)]
        header_dates = week_data["headerDates"] if week_data["headerDates"] else fallback_header

        left_columns = sorted(
            set(
                e["leftPx"]
                for e in week_data["events"]
                if isinstance(e.get("leftPx"), (int, float))
            )
        )

        added_for_week = 0
        for evt in week_data["events"]:
            date = evt.get("date")
            if not date and left_columns and header_dates:
                left = float(evt.get("leftPx", 0))
                best_idx = 0
                best_dist = float("inf")
                for i, col in enumerate(left_columns):
                    dist = abs(col - left)
                    if dist < best_dist:
                        best_dist = dist
                        best_idx = i
                date = header_dates[best_idx] if best_idx < len(header_dates) else None

            times = parse_time_parts(f"{evt.get('timeRaw', '')} {evt.get('text', '')}")

            appointment = Appointment(
                text=evt.get("text", ""),
                description=evt.get("description"),
                date=date,
                start_time=times[0],
                end_time=times[1],
                time_raw=evt.get("timeRaw"),
                css_class=evt.get("class"),
                style=evt.get("style"),
                left_px=evt.get("leftPx"),
                address=evt.get("address"),
                week_label=week_data.get("weekLabel"),
                source=AppointmentSource.SNEXI,
            )

            key = f"{semaine}|{appointment.text}|{appointment.date or 'nodate'}|{appointment.start_time or 'notime'}|{appointment.style}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_appointments.append(appointment)
            added_for_week += 1

        logger.info(f"[EXTRACTION] Week {semaine + 1}: {added_for_week} appointments.")

        if semaine < 3:
            try:
                next_btn_sel = ".fc-next-button, .fc-button-next, button[aria-label='Suivant'], button[title*='suiv' i], .fc-button.fc-button-next"
                await cal_frame.wait_for_selector(next_btn_sel, timeout=5000)
                await cal_frame.click(next_btn_sel)
                await cal_frame.wait_for_timeout(1500)
            except Exception as e:
                logger.warning(f"[EXTRACTION] Cannot advance week {semaine + 1}: {e}")
                break

    logger.info(f"[EXTRACTION] {len(all_appointments)} total appointments extracted.")
    return all_appointments


async def extract_snexi_detail_fields(context, detail_url: str, os_number: str) -> dict:
    """Extract detail fields from Snexi OS detail panel via page.evaluate."""
    extracted = await context.evaluate(
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
                    if (!raw || raw.length > 90) continue;
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
                osNumber: String(meta.osNumber || ''),
                detailUrl: String(meta.detailUrl || location.href || ''),
                address: normalize(byInputOrLabelOrRegex(
                    [/adresse/i, /address/i, /rue/i, /ville/i, /cp/i, /code.?postal/i],
                    [/^adresse$/i, /^adresse\\s+du\\s+bien$/i, /^adresse\\s+intervention$/i],
                    [/\\badresse\\s*(?:du\\s+bien|intervention)?\\s*:\\s*([^\\n]+)/i]
                )),
                owner: normalize(byInputOrLabelOrRegex(
                    [/propriet/i, /owner/i],
                    [/^proprietaire$/i, /^proprietaire\\s+du\\s+bien$/i],
                    [/\\bpropri[ée]taire\\s*:\\s*([^\\n]+)/i]
                )),
                manager: normalize(byInputOrLabelOrRegex(
                    [/gestionnaire/i, /manager/i],
                    [/^gestionnaire$/i],
                    [/\\bgestionnaire\\s*:\\s*([^\\n]+)/i]
                )),
                tenant: normalize(byInputOrLabelOrRegex(
                    [/locataire/i, /tenant/i],
                    [/^locataire$/i, /^locataire\\s+sortant$/i, /^nom\\s+locataire$/i],
                    [/\\blocataire(?:\\s+sortant)?\\s*:\\s*([^\\n]+)/i]
                )),
                tenantMobile: normalize(byInputOrLabelOrRegex(
                    [/portable/i, /telephone.*portable/i, /tel/i, /mobile/i],
                    [/^portable\\s+locataire$/i, /^tel\\.?\\s*locataire(?:\\s+sortant)?$/i, /^telephone\\s+locataire(?:\\s+sortant)?$/i],
                    [/\\b(?:portable|t[ée]l(?:[ée]phone)?)\\s+locataire(?:\\s+sortant)?\\s*:\\s*([^\\n]+)/i]
                )),
                comment: normalize(byInputOrLabelOrRegex(
                    [/comment/i, /observation/i, /note/i],
                    [/^commentaire$/i, /^observations?$/i, /^note(?:s)?$/i],
                    [/\\bcommentaire\\s*:\\s*([^\\n]+)/i, /\\bobservations?\\s*:\\s*([^\\n]+)/i]
                )),
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
                    [/etage/i, /floor/i],
                    [/^etage$/i],
                    [/\\b[ée]tage\\s*:\\s*([^\\n]+)/i]
                )),
                door: normalize(byInputOrLabelOrRegex(
                    [/porte/i, /door/i],
                    [/^porte$/i],
                    [/\\bporte\\s*:\\s*([^\\n]+)/i]
                )),
                digicode: normalize(byInputOrLabelOrRegex(
                    [/digicode/i, /code.*porte/i],
                    [/^digicode$/i, /^code\\s+(?:porte|immeuble)$/i],
                    [/\\bdigicode\\s*:\\s*([^\\n]+)/i, /\\bcode\\s+(?:porte|immeuble)\\s*:\\s*([^\\n]+)/i]
                )),
                building: normalize(byInputOrLabelOrRegex(
                    [/batiment/i, /immeuble/i, /building/i],
                    [/^batiment$/i, /^immeuble$/i],
                    [/\\bb[âa]timent\\s*:\\s*([^\\n]+)/i, /\\bimmeuble\\s*:\\s*([^\\n]+)/i]
                )),
            };
        }""",
        {"detailUrl": detail_url, "osNumber": os_number},
    )
    return extracted


def count_filled_fields(fields: dict) -> int:
    """Count how many detail fields are populated."""
    keys = [
        "address", "owner", "manager", "tenant", "tenantMobile",
        "comment", "keyPickupPlace", "keyDropPlace", "floor",
        "door", "digicode", "building",
    ]
    return sum(1 for k in keys if compact_text(str(fields.get(k, ""))))


async def enrich_snexi_os_from_agenda(page: Page, target: Appointment) -> Optional[dict]:
    """Click one OS in the calendar and extract detail fields."""
    os_number = extract_os_number(f"{target.text} {target.description or ''}")
    if not os_number:
        return None

    start_time = compact_text(target.start_time or "")
    target_date = compact_text(target.date or "")
    target_text = compact_text(target.text).lower()

    cal_frame = await find_calendar_frame_for_enrichment(page)
    if not cal_frame:
        return None

    click_payload = {
        "osNumber": os_number,
        "startTime": start_time,
        "targetDate": target_date,
        "targetText": target_text,
    }

    clicked = await _click_os_in_calendar(cal_frame, click_payload)

    # Try advancing weeks if not found
    for _ in range(3):
        if clicked:
            break
        try:
            next_btn = ".fc-next-button, .fc-button-next, button[aria-label='Suivant']"
            await cal_frame.wait_for_selector(next_btn, timeout=3000)
            await cal_frame.click(next_btn)
            await cal_frame.wait_for_timeout(1500)
            clicked = await _click_os_in_calendar(cal_frame, click_payload)
        except Exception:
            break

    if not clicked:
        return None

    await page.wait_for_timeout(1400)
    try:
        await page.wait_for_function(
            """(num) => {
                const text = String(document.body ? document.body.innerText : '').toLowerCase();
                if (!text) return false;
                const hasNum = num ? text.includes(String(num).toLowerCase()) : false;
                const hasDetails = /(locataire|propri[ée]taire|gestionnaire|digicode|etage|porte|cle|cl[ée]s|batiment|immeuble|adresse)/i.test(text);
                return hasNum || hasDetails;
            }""",
            timeout=5000,
            arg=os_number,
        )
    except Exception:
        pass

    best = None
    contexts = [page] + list(page.frames)
    for ctx in contexts:
        try:
            details = await extract_snexi_detail_fields(ctx, ctx.url if hasattr(ctx, "url") else page.url, os_number)
            score = count_filled_fields(details)
            if not best or score > best["score"]:
                best = {"details": details, "score": score}
        except Exception:
            continue

    # Click back
    await _click_retour(page)

    return best["details"] if best and best["score"] > 0 else None


async def find_calendar_frame_for_enrichment(page: Page) -> Optional[Frame]:
    """Find calendar frame, navigating to agenda URL if needed."""
    for frame in page.frames:
        if "indisponibilites" in frame.url:
            return frame
    for frame in page.frames:
        try:
            has = await frame.evaluate(
                "!!document.querySelector('.fc-event, .fc-time-grid-event, .fc-day-grid-event')"
            )
            if has:
                return frame
        except Exception:
            pass
    return None


async def _click_os_in_calendar(cal_frame: Frame, payload: dict) -> bool:
    """Click a specific OS event in the calendar."""
    return await cal_frame.evaluate(
        """(data) => {
            const num = String(data && data.osNumber ? data.osNumber : '');
            if (!num) return false;
            const start = String(data && data.startTime ? data.startTime : '');
            const targetDate = String(data && data.targetDate ? data.targetDate : '');
            const normalize = (v) => String(v || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            const collectNodes = (root) => Array.from(root.querySelectorAll('.fc-event, a.fc-event, .fc-time-grid-event, .fc-day-grid-event'));
            const agendaTable = document.querySelector('table.fc-agenda-days') || document.querySelector('.fc-agenda-days');
            const tableNodes = agendaTable ? collectNodes(agendaTable) : [];
            const nodes = tableNodes.length ? tableNodes : collectNodes(document);
            const withNum = nodes.filter((el) => {
                const txt = normalize(el.innerText || el.textContent || '');
                if (!txt) return false;
                if (!txt.includes(`os n°${num}`) && !txt.includes(`os n${num}`) && !txt.includes(num)) return false;
                if (start && !txt.includes(start.toLowerCase())) return false;
                if (targetDate) {
                    const dateFromParent = (el.closest('[data-date]') && el.closest('[data-date]').getAttribute('data-date')) || '';
                    if (dateFromParent && dateFromParent !== targetDate) return false;
                }
                return true;
            });
            const target = withNum[0] || null;
            if (!target) return false;
            target.click();
            return true;
        }""",
        payload,
    )


async def _click_retour(page: Page) -> bool:
    """Click the 'Retour' button to go back from OS detail view."""
    icon_selectors = [
        "#button-1501-btnIconEl",
        "#button-1161-btnIconEl",
        'span.x-btn-icon-el[id$="-btnIconEl"]',
    ]

    for ctx in [page] + list(page.frames):
        for selector in icon_selectors:
            try:
                clicked = await ctx.evaluate(
                    """(sel) => {
                        const icon = document.querySelector(sel);
                        if (!icon) return false;
                        const clickable = icon.closest('a,button,[role="button"],.x-btn,.x-btn-default-toolbar-small') || icon.parentElement || icon;
                        if (!clickable) return false;
                        clickable.click();
                        return true;
                    }""",
                    selector,
                )
                if clicked:
                    await page.wait_for_timeout(900)
                    return True
            except Exception:
                continue

    # Text fallback
    for ctx in [page] + list(page.frames):
        try:
            clicked = await ctx.evaluate("""() => {
                const nodes = Array.from(document.querySelectorAll('a,button,span,div,input[type="button"],input[type="submit"]'));
                const target = nodes.find((el) => {
                    const txt = (el.innerText || el.textContent || el.value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    return txt === 'retour' || txt.startsWith('retour ');
                });
                if (!target) return false;
                (target.closest('a,button,[role="button"]') || target).click();
                return true;
            }""")
            if clicked:
                await page.wait_for_timeout(900)
                return True
        except Exception:
            continue

    try:
        await page.go_back(wait_until="networkidle", timeout=8000)
        await page.wait_for_timeout(700)
        return True
    except Exception:
        return False


async def enrich_snexi_appointments(page: Page, events: list[Appointment], settings: Settings) -> list[Appointment]:
    """Enrich all Snexi blue/green events with detail fields from detail panels."""
    if not settings.snexi_enrich_details:
        return events

    snexi_events = [e for e in events if e.source == AppointmentSource.SNEXI]
    if not snexi_events:
        return events

    # Find events that have OS numbers and are blue/green (entree/sortie)
    targets = []
    seen_keys: set[str] = set()

    for evt in snexi_events:
        os_num = extract_os_number(f"{evt.text} {evt.description or ''}")
        if not os_num:
            continue
        is_blue_green = evt.style and (
            "rgb(18, 17, 171)" in evt.style
            or "rgb(17, 138, 123)" in evt.style
        )
        is_indispo = bool(re.search(r"indisponibilit[ée]", evt.text or "", re.IGNORECASE))
        has_os = bool(re.search(r"\bos\s*n[°º]?\s*\d{5,}", f"{evt.text} {evt.description or ''}", re.IGNORECASE))
        is_trajet = bool(re.search(r"\btrajet\b", evt.text or "", re.IGNORECASE))

        if not is_blue_green or is_indispo or not has_os or is_trajet:
            continue

        key = f"{evt.date or 'nodate'}|{evt.start_time or 'notime'}|{os_num}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        targets.append((evt, os_num, key))

    targets.sort(key=lambda t: f"{t[0].date or ''} {t[0].start_time or ''}")

    if not targets:
        return events

    details_by_os: dict[str, dict] = {}
    for i, (target, os_num, key) in enumerate(targets, 1):
        try:
            logger.info(f"[SNEXI][DETAIL] Click OS {os_num} ({target.date} {target.start_time})")
            fields = await enrich_snexi_os_from_agenda(page, target)
            if not fields:
                logger.warning(f"[SNEXI][DETAIL] OS {os_num}: inline panel not found.")
                continue
            score = count_filled_fields(fields)
            if score > 0:
                existing = details_by_os.get(os_num)
                if not existing or count_filled_fields(existing) < score:
                    details_by_os[os_num] = fields
            if i % 5 == 0 or i == len(targets):
                logger.info(f"[SNEXI][DETAIL] {i}/{len(targets)} blue/green appointments clicked.")
        except Exception as e:
            logger.warning(f"[SNEXI][DETAIL] OS {os_num}: enrichment failed ({e}).")

    if not details_by_os:
        return events

    enriched = []
    for evt in events:
        if evt.source != AppointmentSource.SNEXI:
            enriched.append(evt)
            continue
        os_num = extract_os_number(f"{evt.text} {evt.description or ''}")
        if not os_num or os_num not in details_by_os:
            enriched.append(evt)
            continue
        fields = details_by_os[os_num]
        enriched.append(evt.model_copy(update={
            "os_number": os_num,
            "detail_url": fields.get("detailUrl") or evt.detail_url,
            "address": fields.get("address") or evt.address,
            "owner": fields.get("owner") or evt.owner,
            "manager": fields.get("manager") or evt.manager,
            "tenant": fields.get("tenant") or evt.tenant,
            "tenant_mobile": fields.get("tenantMobile") or evt.tenant_mobile,
            "comment": fields.get("comment") or evt.comment,
            "key_pickup_place": fields.get("keyPickupPlace") or evt.key_pickup_place,
            "key_drop_place": fields.get("keyDropPlace") or evt.key_drop_place,
            "floor": fields.get("floor") or evt.floor,
            "door": fields.get("door") or evt.door,
            "digicode": fields.get("digicode") or evt.digicode,
            "building": fields.get("building") or evt.building,
        }))

    return enriched
