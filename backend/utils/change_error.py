"""The error every Google change service (calendar, documents) raises for something the person can act on: its message is written to be shown as it is, and never carries Google's own text.
Lives apart from both services so the card mechanism (backend/services/change_cards.py) can catch it without knowing which service raised it."""


class ChangeError(Exception):
    pass
