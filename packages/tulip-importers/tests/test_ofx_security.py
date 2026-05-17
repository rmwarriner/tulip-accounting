"""Lock the XXE-immunity property of the OFX parser.

`ofxtools` uses a regex tag tokeniser, not an XML parser, so external-entity
resolution is structurally impossible. These tests assert payloads that would
trip a real XML parser either parse benignly (no entity expansion) or surface
as a typed ``OfxParseError`` — never as silent entity resolution or as a
DoS-grade entity-expansion attack.

Reference: THREAT_MODEL.md §5.2 (importers handle untrusted input).
"""

from __future__ import annotations

import pytest

from tulip_importers.ofx import OfxParseError, parse

# Classic Billion Laughs payload — would explode in any DTD-processing XML
# parser. The regex tokeniser sees no <STMTTRN> blocks and surfaces a typed
# parse failure instead.
BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<lolz>&lol3;</lolz>
"""


# External-entity reference targeting /etc/passwd — a real XXE attempt. The
# regex tokeniser does not resolve entities; the payload must not surface the
# file contents under any circumstance.
EXTERNAL_ENTITY = b"""<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<foo>&xxe;</foo>
"""


class TestNoXxeSurface:
    def test_billion_laughs_does_not_explode(self):
        """A DTD-processing parser would OOM on this input; ours must not."""
        with pytest.raises(OfxParseError):
            parse(BILLION_LAUGHS)

    def test_external_entity_is_not_resolved(self):
        """External entity reference must not pull file contents into output."""
        with pytest.raises(OfxParseError):
            parse(EXTERNAL_ENTITY)
