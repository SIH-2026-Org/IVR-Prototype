"""Local HTTP integration against a running DRE. No Twilio call or audio upload."""
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conversation_manager import ConversationManager
from debug_output import format_final_match
from session_store import InMemorySessionStore


async def main():
    manager = ConversationManager(InMemorySessionStore())
    session = manager.start_session('LOCAL_MULTILINGUAL_DEMO', 'en', 'en-IN')
    text = 'main Rajasthan se hoon aur dairy ka kaam shuru karna hai. My age is 28.'
    print('CALLER:', text)
    decision = await manager.handle_turn(session.call_sid, 'en', 'en-IN', speech=text, confidence='0.95')
    while decision.action == 'ask':
        print('IVR:', decision.prompt)
        if set(session.current_fields) == {'income_annual', 'project_cost'}:
            answer = {'speech': 'Meri annual family income one lakh twenty thousand hai aur project cost do lakh hai.'}
        elif decision.field == 'social_category':
            answer = {'digits': '1'}
        elif decision.field == 'gender':
            answer = {'digits': '1'}
        elif decision.field == 'income_annual':
            answer = {'speech': 'one lakh twenty thousand'}
        elif decision.field == 'project_cost':
            answer = {'speech': 'do lakh'}
        else:
            raise AssertionError(f'Unexpected required field: {decision.field}')
        print('CALLER:', answer)
        decision = await manager.handle_turn(session.call_sid, 'en', 'en-IN', confidence='0.95', **answer)
    assert decision.action == 'complete', decision
    assert decision.result['normalized_profile']['income_annual'] == 120000
    assert decision.result['normalized_profile']['project_cost'] == 200000
    assert session.callbacks_used <= 9, session.callbacks_used
    print(format_final_match(session.call_sid, decision.result))
    print('IVR:', decision.prompt)
    print('TOTAL CALLBACKS INCLUDING THREE OPENING CALLBACKS:', session.callbacks_used)


if __name__ == '__main__':
    asyncio.run(main())
