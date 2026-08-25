# -*- coding: utf-8 -*-
"""fragment_api.py — Fragment API alohida modul."""

import html
import logging
import time
import traceback as _tb

from FragmentAPI import FragmentClient

try:
    from config import TON_SEED, TON_WALLET_VERSION, TONAPI_KEY
except ImportError:
    from config import TON_SEED, TON_WALLET_VERSION
    TONAPI_KEY = None

try:
    from config import FRAGMENT_COOKIES
except ImportError:
    FRAGMENT_COOKIES = ""

PURCHASE_ENABLED = True
PAYMENT_METHOD = "ton"
WALLET_ADDRESS = None

STARS_PACKAGES = [50, 75, 100, 150, 250, 350, 500, 750, 1000]
STARS_PRICE_BUFFER_PCT = 0
STARS_PRICE_CACHE_TTL = 45
_stars_price_cache = {}

logger = logging.getLogger(__name__)


def _fragment_cookies():
    if isinstance(FRAGMENT_COOKIES, dict):
        return {
            str(k).strip(): str(v).strip()
            for k, v in FRAGMENT_COOKIES.items()
            if str(v).strip()
        }

    raw = str(FRAGMENT_COOKIES or "").strip()
    out = {}

    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()

    return out


def _fragment_kwargs():
    cookies = _fragment_cookies()

    if not cookies:
        raise RuntimeError("Fragment cookies sozlanmagan. config.py ni tekshiring.")

    return {
        "seed": TON_SEED,
        "wallet_version": TON_WALLET_VERSION,
        "cookies": cookies,
        "api_key": TONAPI_KEY,
        "stats_enabled": False,
    }


async def _get_wallet_address():
    global WALLET_ADDRESS

    if WALLET_ADDRESS:
        return WALLET_ADDRESS

    async with FragmentClient(**_fragment_kwargs()) as client:
        wallet = await client.get_wallet()
        address = getattr(wallet, "address", None)

        if not address:
            raise RuntimeError(f"Wallet address olinmadi: {type(wallet).__name__}")

        WALLET_ADDRESS = str(address)

    return WALLET_ADDRESS


async def _get_ton_balance():
    async with FragmentClient(**_fragment_kwargs()) as client:
        wallet = await client.get_wallet()

        for attr in ("balance_ton", "ton_balance", "balance"):
            value = getattr(wallet, attr, None)
            if value is not None:
                return round(float(value), 4)

        raise RuntimeError(
            f"Wallet balans atributi topilmadi: {type(wallet).__name__}"
        )


async def get_fragment_stars_price(quantity, use_cache=True):
    now = time.time()

    if use_cache:
        cached = _stars_price_cache.get(quantity)
        if cached and (now - cached[0]) < STARS_PRICE_CACHE_TTL:
            return cached[1]

    async with FragmentClient(**_fragment_kwargs()) as client:
        result = await client.get_stars_price(quantity)

    ton_price = float(result.ton_price)
    _stars_price_cache[quantity] = (now, ton_price)
    return ton_price


async def get_fragment_stars_prices_bulk(quantities):
    now = time.time()
    results = {}
    to_fetch = []

    for quantity in quantities:
        cached = _stars_price_cache.get(quantity)

        if cached and (now - cached[0]) < STARS_PRICE_CACHE_TTL:
            results[quantity] = cached[1]
        else:
            to_fetch.append(quantity)

    if to_fetch:
        async with FragmentClient(**_fragment_kwargs()) as client:
            for quantity in to_fetch:
                try:
                    result = await client.get_stars_price(quantity)
                    price = float(result.ton_price)
                    _stars_price_cache[quantity] = (time.time(), price)
                    results[quantity] = price
                except Exception as e:
                    logger.error(
                        "Stars narxini olishda xato (qty=%s): %s", quantity, e
                    )
                    results[quantity] = None

    return results


async def get_fragment_wallet_info():
    return {
        "address": await _get_wallet_address(),
        "balance_ton": await _get_ton_balance(),
    }


def fragment_cookie_status():
    cookies = _fragment_cookies()
    required = ("stel_ssid", "stel_token")
    missing = [name for name in required if not cookies.get(name)]
    return {"ok": not missing, "missing": missing}


async def api_buy_stars(username, amount, order_id=None):
    if not PURCHASE_ENABLED:
        return {"success": False, "error": "Xizmat vaqtincha to'xtatilgan."}

    try:
        clean = username.lstrip("@").strip()

        ton = await _get_ton_balance()
        real_price = await get_fragment_stars_price(amount, use_cache=False)
        required = round(
            real_price * (1 + STARS_PRICE_BUFFER_PCT / 100), 4
        )

        if ton < required:
            return {
                "success": False,
                "error": (
                    f"Bot walletida yetarli TON yo'q "
                    f"(Kerak: {required:.4f} TON, mavjud: {ton:.4f} TON)."
                ),
            }

        async with FragmentClient(**_fragment_kwargs()) as client:
            result = await client.purchase_stars(
                clean,
                amount,
                payment_method=PAYMENT_METHOD,
                show_sender=False,
            )

        return {
            "success": True,
            "result": {
                "id": getattr(result, "transaction_id", str(order_id)),
                "username": getattr(result, "username", clean),
                "amount": getattr(result, "amount", amount),
            },
        }

    except Exception as e:
        logger.error("Stars buy xatosi: %s\n%s", e, _tb.format_exc())
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


async def api_buy_premium(username, duration_months, order_id=None):
    if not PURCHASE_ENABLED:
        return {"success": False, "error": "Xizmat vaqtincha to'xtatilgan."}

    try:
        clean = username.lstrip("@").strip()

        async with FragmentClient(**_fragment_kwargs()) as client:
            result = await client.purchase_premium(
                clean,
                duration_months,
                payment_method=PAYMENT_METHOD,
            )

        return {
            "success": True,
            "result": {
                "id": getattr(result, "transaction_id", str(order_id)),
                "username": getattr(result, "username", clean),
                "duration": getattr(result, "duration", duration_months),
            },
        }

    except Exception as e:
        logger.error("Premium buy xatosi: %s\n%s", e, _tb.format_exc())
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


async def fragment_wallet_command(update, context):
    try:
        info = await get_fragment_wallet_info()

        await update.message.reply_text(
            "💎 <b>Fragment TON Wallet</b>\n\n"
            f"📬 <b>Address:</b>\n<code>{html.escape(info['address'])}</code>\n\n"
            f"💰 <b>Balance:</b> {info['balance_ton']:.4f} TON",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Fragment wallet xatosi")
        await update.message.reply_text(
            f"❌ Fragment wallet xatosi: <code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )


async def fragment_cookie_status_command(update, context):
    status = fragment_cookie_status()

    if status["ok"]:
        text = (
            "✅ Fragment cookie konfiguratsiyasi topildi.\n"
            "🔐 Qiymatlar ko‘rsatilmaydi."
        )
    else:
        text = "❌ Cookie yetishmayapti: " + ", ".join(status["missing"])

    await update.message.reply_text(text)
