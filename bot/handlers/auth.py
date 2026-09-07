"""Авторизация пользователя в Google (OAuth2), ручной code-flow для Desktop App."""

import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from bot.keyboards.keyboards import BTN_AUTH, main_menu_keyboard, cancel_keyboard
from services import google_service, oauth_server
from services.google_service import GoogleAuthError

logger = logging.getLogger(__name__)
router = Router(name="auth")


class AuthStates(StatesGroup):
    waiting_for_code = State()


@router.message(F.text == BTN_AUTH)
async def start_auth(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    if google_service.is_authorized(user_id):
        await message.answer(
            "Ви вже авторизовані в Google ✅\n"
            "Якщо хочете переавторизуватися — спочатку натисніть «🚪 Вийти з Google».",
            reply_markup=main_menu_keyboard(user_id),
        )
        return

    try:
        auth_url, oauth_state = google_service.get_authorization_url()
    except GoogleAuthError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    oauth_server.register_pending(oauth_state, user_id)

    await state.update_data(oauth_state=oauth_state)
    await state.set_state(AuthStates.waiting_for_code)

    await message.answer(
        "Перейдіть за посиланням і дозвольте доступ до Google Classroom:\n"
        f"{auth_url}\n\n"
        "Далі нічого копіювати не потрібно — щойно ви дозволите доступ, "
        "я сам усе підхоплю і напишу сюди «✅ Авторизація пройшла успішно».\n\n"
        "Якщо раптом за хвилину нічого не прийшло (наприклад, недоступний "
        "callback-сервер), можна надіслати код або посилання з параметром "
        "code=... сюди вручну.",
        reply_markup=cancel_keyboard(),
        disable_web_page_preview=True,
    )


@router.message(AuthStates.waiting_for_code)
async def receive_auth_code(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    code_or_url = message.text or ""

    try:
        google_service.exchange_code_and_save(user_id, code_or_url)
    except GoogleAuthError as exc:
        await message.answer(
            f"⚠️ Не вдалося авторизуватися: {exc}\n"
            "Спробуйте ще раз надіслати код або посилання, або натисніть «Скасувати».",
            reply_markup=cancel_keyboard(),
        )
        return

    await state.clear()
    await message.answer(
        "✅ Авторизація пройшла успішно! Тепер можна переглядати домашні завдання.",
        reply_markup=main_menu_keyboard(user_id),
    )


@router.callback_query(F.data == "cancel", AuthStates.waiting_for_code)
async def cancel_auth(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Авторизацію скасовано.")
    await callback.message.answer("Головне меню:", reply_markup=main_menu_keyboard(callback.from_user.id))
    await callback.answer()
