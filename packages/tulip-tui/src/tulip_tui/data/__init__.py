"""Data-layer adapters for the Tulip TUI.

Pure Python: assemble domain-shaped value objects from the API's JSON
responses so the Textual layer never has to reach into raw dicts. Lets
screens be tested against in-memory fixtures without spinning up a
client or stubbing httpx.
"""
