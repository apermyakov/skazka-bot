# -*- coding: utf-8 -*-
"""FSM states for fairy tale creation flow."""

from aiogram.fsm.state import State, StatesGroup


class CreateFairyTale(StatesGroup):
    waiting_topic = State()       # User sends topic + child info
    confirming_input = State()    # User confirms before screenplay generation
    reviewing_story = State()     # User reviews story text
    waiting_edits = State()       # User sends edits
    generating = State()          # Audio generation in progress
