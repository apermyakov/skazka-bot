# -*- coding: utf-8 -*-
"""FSM states for fairy tale creation flow."""

from aiogram.fsm.state import State, StatesGroup


class CreateFairyTale(StatesGroup):
    waiting_topic = State()       # User sends topic + child info
    reviewing_story = State()     # User reviews text, can edit or approve
    waiting_edits = State()       # User sends edits for the story
    generating = State()          # Audio generation in progress
