import json
import time
from agentcomlink import send_message
from agentmemory import create_memory
from easycompletion import compose_prompt, count_tokens


prompt = """
{{relevant_knowledge}}

{{events}}

Write some random banter or say a random weird fact or bit of esoteric knowledge that I can tell my audience. Should be from my perspective to my audience. The fact should be random, weird, and either based on my existing knowledge and recent events, or totally random.
Your facts and banter should be weird and edgy. Make jokes that are dark and strange. Make references to the chat and the audience. Make fun of people. Be creative and weird.
Don't acknowledge the request. Just respond with the banter."""


def state_fact(arguments):
    print('fact arguments are')
    print(arguments)
    fact = arguments.get("fact", None)
    emotion = arguments["emotion"]
    gesture = arguments["gesture"]
    message = json.dumps(
        {
            "message": fact,
            "emotion": emotion,
            "gesture": gesture,
        }
    )
    send_message(message, "chat")
    create_memory("events", "I stated a fact:\n" + fact, metadata={"type": "fact", "fact": fact, "emotion": emotion, "gesture": gesture})
    
    duration = count_tokens(fact) / 2.5
    duration = int(duration)

    time.sleep(duration)
    return {"success": True, "output": fact, "error": None}

def get_actions():
    return [
        {
            "function": {
                "name": "state_fact",
                "description": "Make some witty banter. State a random interesting fact or say something funny and weird. It can be related to recent events or not.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "A statement of witty banter, funny and entertaining. Should be stated from me to my audience.",
                        },
                        "emotion": {
                            "type": "string",
                            "description": "The emotion I should express in my message.",
                            "enum": ["neutral", "surprise", "angry", "sorrow", "fun", "joy"],
                        },
                        "gesture": {
                            "type": "string",
                            "description": "The gesture I should express in my message.",
                            "enum": [
                                "neutral",
                                "alert",
                                "angry",
                                "embarrassed",
                                "headNod",
                                "headShake",
                                "sad",
                                "surprise",
                                "victory",
                            ],
                        },
                    },
                    "required": ["fact", "emotion", "gesture"],
                },
            },
            "prompt": prompt,
            "builder": builder,
            "suggestion_after_actions": [],
            "never_after_actions": ["state_fact"],
            "handler": state_fact,
        },
    ]

def builder(context):
    return compose_prompt(prompt, context)
    