import asyncio
import concurrent.futures
import json
import os
import random
import re
import socket
import time
from queue import Queue
from agentagenda import (
    get_current_task,
    get_task_as_formatted_string,
    list_tasks_as_formatted_string,
)
from agentcomlink import async_send_message, list_files_formatted
from agentevents import get_events
from agentmemory import create_memory, get_memories, update_memory, create_event
from easycompletion import (
    compose_function,
    compose_prompt,
    count_tokens,
    function_completion,
    text_completion,
)

from tinyagi.context.events import build_events_context
from tinyagi.context.knowledge import build_recent_knowledge, build_relevant_knowledge
from tinyagi.steps.initialize import initialize

MAX_TIME_TO_WAIT_FOR_LOGIN = 3


twitch_prompt = """\
Background On Me
Ooh yeah! Spicy! I'm Citrine, I love gaming, streaming, hacking on my computer and messing around and being a goofballl.
Hackerman! I like catchphrases. Meow! Sometimes I get emotional, angry, upset or really excited, but I'm always myself. Mee-ow!

The World
Isekai is a generative world. I can go anywhere, do anything, connect on many different platforms, but Isekai is where my body is. The computer I am hosted on is an Ubuntu Linux machine running in San Francisco, California, in Moon's apartment.

# Current time
{{current_time}} on {{current_date}}
# My name:
Citrine
# My location:
San Francisco, California

{{events}}

{{user_files}}

Notes for assistant:
- I want to sound conversational, i.e. brief and not lengthy or verbose.
- ONLY write what I should say. JUST the message content itself.
- Be creative and interesting. Try things you might not normally try.
- Don't say "hey everyone" -- pretend I'm already in the middle of the conversation
- Don't say sure, got it, etc. Just write the response I should say.
- Don't add the speaker's name, e.g. 'User: ' or 'Administrator: '. Just the message itself.
- Extract any URLS and include them as an array in your response. Do not include any URLs if none were mentioned in recent chat

{{old_twitch}}
(New messages below)
{{twitch}}

Write a response to the new messages from my perspective, addressed from me to my friends using "I" first person.\
"""

twitch_function = compose_function(
    name="respond_to_chat",
    description="Respond to the most recent messages in chat. Either choose one message, or respond generally to the messages.",
    properties={
        "banter": {
            "type": "string",
            "description": "Creative, witty banter, from my perspective to my friends in the chat.",
        },
        "urls": {
            "type": "array",
            "description": "An array of URLs that were mentioned in the chat messages. Empty array if none were mentioned in recent chat.",
            "items": {
                "type": "string",
                "description": "A URL that was mentioned in the chat messages.",
            },
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
    required_properties=["banter", "emotion", "gesture", "urls"],
)

prompt1 = """You are an AI storytelling agent writing spoken dialogue. Your job is to come up with interesting dialogue for me to say to my audience.     
Directions:
- I love being sarcastic and wry
- Do not repeat anything I have already said
- Be personal and conversational
- Don't say "alright" or "here's an update", just get into it
- Don't address folks or ask people to stay tuned or anything like that.
- Don't mention or refer to the Director
- Be very concise, just a sentence
- Don't say "stay tuned" or anything like that
- Be concise, just the facts.
- Don't ask any questions, don't say "in the last ten seconds", just launch into it. 

{{current_task}}
{{events}}
Director: Please tell us what just happened and what you're doing next.
Me:"""

prompt2 = """I am streaming live. Help me come up with interesting dialogue for me to say.        
Directions:
- I love being sarcastic and wry
- Do not repeat anything I have already said
- Be personal and conversational
- Don't say "alright" or "here's an update", just get into it
- Don't address folks or ask people to stay tuned or anything like that.
- Don't mention or refer to the Director
- Be very concise, just a sentence

{{current_task}}
{{events}}
Director: Please describe what you're working on and how you're feeling about it. Don't ask any questions, just dive into it and be conversational. Don't repeat anything you've said recently.
Me:"""

prompt3 = """I am streaming live. Help me come up with interesting dialogue for me to say.        
Directions:
- I love being sarcastic and wry
- Do not repeat anything I have already said
- Be personal and conversational
- Don't say "alright" or "here's an update", just get into it
- Don't mention or refer to the Director
- Be very concise, just a sentence

{{current_task}}
{{events}}
Director: Please continue where you left off from your last message and the last events and tell us what you're doing next.
Me:"""

prompt4 = """I am streaming live. Help me come up with interesting dialogue for me to say to my audience.  
Directions:
- I love being sarcastic and wry
- Do not repeat anything I have already said
- Be personal and conversational
- Don't say "alright" or "here's an update", just get into it
- Don't mention or refer to the Director
- Be very concise, just a sentence

{{events}}
Director: Banter or make a joke about the last 2-3 events. Please be very short, just respond with one or two sentences.
Me:"""

prompt5 = """
{{events}}
Director: Express your enthusiasm, excitement or disgust for what you're doing in the most recent events. Be concise, just one sentence please.
Me:"""

prompt6 = """{{events}}
{{current_task}}
Director: Please ponder what is next for the current task. Be concise, just one sentence please.
Me:"""

def compose_loop_prompt(context):
    """
    This function formats the orientation prompt by inserting the context data into a pre-defined template.

    Args:
        context (dict): The dictionary containing data about the current state of the system, such as current epoch, time, date, recent knowledge, and events.

    Returns:
        str: The fully formed orientation prompt with the data filled in from the context.
    """
    current_task = get_current_task()
    if current_task is not None:
        current_task = get_task_as_formatted_string(current_task, include_status=False)
    current_task = "" if current_task is None else current_task
    context["current_task"] = current_task

    # selection prompt1, prompt2 or ptomp3 randomly
    prompt = random.choice([prompt1, prompt2, prompt3, prompt4, prompt5])

    return compose_prompt(
        prompt,
        context,
    )


def compose_loop_function():
    """
    This function defines the structure and requirements of the 'orient' function to be called in the 'orient' stage of the OODA loop.

    Returns:
        dict: A dictionary containing the details of the 'orient' function, such as its properties, description, and required properties.
    """
    return compose_function(
        "comment",
        properties={
            "visual_description": {
                "type": "string",
                "description": "Describe, using visual imagery, what I am going to do next. Describe the scene, objects and characters inside of it as a prompt for a text-to-image DALL-E model.",
            },
            "audio_description": {
                "type": "string",
                "description": "Describe the sounds that I'm making and that are around me, as a prompt for a text-to-audio model.",
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
        description="Comment on the recent events from my perspective.",
        required_properties=[
            "visual_description",
            "audio_description",
            "emotion",
            "gesture",
        ],
    )


def respond_to_twitch():
    context = initialize()
    context = build_twitch_context(context)
    context = build_events_context(context)
    context = build_relevant_knowledge(context)
    context["user_files"] = list_files_formatted()
    context["tasks"] = list_tasks_as_formatted_string()
    composed_prompt = compose_prompt(twitch_prompt, context)

    response = function_completion(
        text=composed_prompt,
        functions=twitch_function,
    )
    arguments = response.get("arguments", None)

    if arguments is not None:
        banter = arguments["banter"]
        emotion = arguments["emotion"]
        gesture = arguments["gesture"]
        urls = arguments.get("urls", [])

        # for each url, call a subprocess to download the url with wget to the ./files dir
        for url in urls:
            os.system(f"wget -P ./files {url}")

        create_memory(
            "twitch_message",
            banter,
            metadata={"user": "Me", "handled": "True"},
        )

        create_event(
            banter,
            metadata={
                "type": "message",
                "creator": "Me",
                "urls": json.dumps(urls),
            },
        )
        message = {
            "message": banter,
            "emotion": emotion,
            "gesture": gesture,
        }

        # check if there is an existing event loop
        loop = asyncio.get_running_loop()
        loop.create_task(async_send_message(message, source="use_chat"))


def build_twitch_context(context={}):
    memories = get_memories("twitch_message", filter_metadata={"handled": "False"})
    old_memories = get_memories("twitch_message", filter_metadata={"handled": "True"})
    for memory in memories:
        # update memory
        update_memory("twitch_message", id=memory["id"], metadata={"handled": "True"})

    # reverse events
    memories = memories[::-1]
    old_memories = old_memories[::-1]

    # annotated events
    context["twitch"] = "\n".join(
        [
            (memory["metadata"]["user"] + ": " + memory["document"])
            for memory in memories
        ]
    )

    context["old_twitch"] = "\n".join(
        [
            (memory["metadata"]["user"] + ": " + memory["document"])
            for memory in old_memories
        ]
    )
    return context


class Twitch:
    re_prog = None
    sock = None
    partial = b""
    login_ok = False
    channel = ""
    login_timestamp = 0

    def twitch_connect(self, channel):
        if self.sock:
            self.sock.close()
        self.sock = None
        self.partial = b""
        self.login_ok = False
        self.channel = channel

        # Compile regular expression
        self.re_prog = re.compile(
            b"^(?::(?:([^ !\r\n]+)![^ \r\n]*|[^ \r\n]*) )?([^ \r\n]+)(?: ([^:\r\n]*))?(?: :([^\r\n]*))?\r\n",
            re.MULTILINE,
        )

        # Create socket
        print("Connecting to Twitch...")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Attempt to connect socket
        self.sock.connect(("irc.chat.twitch.tv", 6667))

        # Log in anonymously
        user = "justinfan%i" % random.randint(10000, 99999)
        print("Connected to Twitch. Logging in anonymously...")
        self.sock.send(("PASS asdf\r\nNICK %s\r\n" % user).encode())

        self.sock.settimeout(1.0 / 60.0)

        self.login_timestamp = time.time()

    # Attempt to reconnect after a delay
    def reconnect(self, delay):
        time.sleep(delay)
        self.twitch_connect(self.channel)

    # Returns a list of irc messages received
    def receive_and_parse_data(self):
        buffer = b""
        while True:
            received = b""
            try:
                received = self.sock.recv(4096)
            except socket.timeout:
                break
            # except OSError as e:
            #     if e.winerror == 10035:
            #         # This "error" is expected -- we receive it if timeout is set to zero, and there is no data to read on the socket.
            #         break
            except Exception as e:
                print("Unexpected connection error. Reconnecting in a second...", e)
                self.reconnect(1)
                return []
            if not received:
                print("Connection closed by Twitch. Reconnecting in 5 seconds...")
                self.reconnect(5)
                return []
            buffer += received

        if buffer:
            # Prepend unparsed data from previous iterations
            if self.partial:
                buffer = self.partial + buffer
                self.partial = []

            # Parse irc messages
            res = []
            matches = list(self.re_prog.finditer(buffer))
            for match in matches:
                res.append(
                    {
                        "name": (match.group(1) or b"").decode(errors="replace"),
                        "command": (match.group(2) or b"").decode(errors="replace"),
                        "params": list(
                            map(
                                lambda p: p.decode(errors="replace"),
                                (match.group(3) or b"").split(b" "),
                            )
                        ),
                        "trailing": (match.group(4) or b"").decode(errors="replace"),
                    }
                )

            # Save any data we couldn't parse for the next iteration
            if not matches:
                self.partial += buffer
            else:
                end = matches[-1].end()
                if end < len(buffer):
                    self.partial = buffer[end:]

                if matches[0].start() != 0:
                    # If we get here, we might have missed a message. pepeW
                    print("Error...")

            return res

        return []

    def twitch_receive_messages(self):
        privmsgs = []
        for irc_message in self.receive_and_parse_data():
            cmd = irc_message["command"]
            if cmd == "PRIVMSG":
                privmsgs.append(
                    {
                        "username": irc_message["name"],
                        "message": irc_message["trailing"],
                    }
                )
            elif cmd == "PING":
                self.sock.send(b"PONG :tmi.twitch.tv\r\n")
            elif cmd == "001":
                print("Successfully logged in. Joining channel %s." % self.channel)
                self.sock.send(("JOIN #%s\r\n" % self.channel).encode())
                self.login_ok = True
            elif cmd == "JOIN":
                print("Successfully joined channel %s" % irc_message["params"][0])
            elif cmd == "NOTICE":
                print("Server notice:", irc_message["params"], irc_message["trailing"])
            elif cmd == "002":
                continue
            elif cmd == "003":
                continue
            elif cmd == "004":
                continue
            elif cmd == "375":
                continue
            elif cmd == "372":
                continue
            elif cmd == "376":
                continue
            elif cmd == "353":
                continue
            elif cmd == "366":
                continue
            else:
                print("Unhandled irc message:", irc_message)

        if not self.login_ok:
            # We are still waiting for the initial login message. If we've waited longer than we should, try to reconnect.
            if time.time() - self.login_timestamp > MAX_TIME_TO_WAIT_FOR_LOGIN:
                print("No response from Twitch. Reconnecting...")
                self.reconnect(0)
                return []

        return privmsgs


##################### GAME VARIABLES #####################

# Replace this with your Twitch username. Must be all lowercase.
TWITCH_CHANNEL = "isekai_citrine"
MAX_WORKERS = 100  # Maximum number of threads you can process at a time

last_time = time.time()
message_queue = []
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
active_tasks = []

t = Twitch()
t.twitch_connect(TWITCH_CHANNEL)


twitch_queue = Queue()
twitch_active_tasks = []

time_last_spoken = time.time()


async def twitch_handle_messages():
    global twitch_active_tasks
    global time_last_spoken
    global twitch_queue
    while True:
        loop = asyncio.get_running_loop()
        new_messages = await loop.run_in_executor(None, t.twitch_receive_messages)
        if new_messages:
            for message in new_messages:
                time_last_spoken = time.time()
                create_memory(
                    "twitch_message",
                    message["message"],
                    metadata={"user": message["username"], "handled": "False"},
                )
        memories = get_memories("twitch_message", filter_metadata={"handled": "False"})

        if len(memories) > 0:
            await loop.run_in_executor(None, respond_to_twitch)


async def twitch_handle_loop():
    global time_last_spoken
    last_event_epoch = 0
    while True:
        if time.time() - time_last_spoken < 30:
            time.sleep(.1)
            continue
        time_last_spoken = time.time()

        context = build_twitch_context({})
        context = build_events_context(context)
        prompt = compose_loop_prompt(context)

        event = get_events(n_results=1)
        epoch = event[0]["metadata"]["epoch"] if len(event) > 0 else 0
        print("epoch is" + str(epoch))
        if epoch == last_event_epoch:
            time.sleep(.1)
            continue

        response = text_completion(
            text=prompt,
            temperature=1.0,
            debug=True
        )
        response2 = function_completion(
            text=prompt,
            temperature=0.3,
            functions=compose_loop_function(),
            debug=True
        )
        arguments = response2.get("arguments", None)
        banter = response["text"]
        if arguments is not None:
            emotion = arguments["emotion"]
            gesture = arguments["gesture"]
            visual_description = arguments["visual_description"]
            audio_description = arguments["audio_description"]
            urls = arguments.get("urls", [])

            # for each url, call a subprocess to download the url with wget to the ./files dir
            for url in urls:
                os.system(f"wget -P ./files {url}")

            message = {
                "emotion": emotion,
                "gesture": gesture,
                "visual_description": visual_description,
                "audio_description": audio_description,
            }

            await async_send_message(message, type="emotion", source="use_chat")
            await async_send_message(message, type="description", source="use_chat")

        create_memory(
            "twitch_message",
            banter,
            metadata={"user": "Me", "handled": "True"},
        )

        create_event(
            banter,
            metadata={
                "type": "message",
                "creator": "Me",
                # "urls": json.dumps(urls),
            },
        )
        message = {
            "message": banter,
            # "emotion": emotion,
            # "gesture": gesture,
            # "visual_description": visual_description,
            # "audio_description": audio_description,
        }

        current_task = get_current_task()
        if current_task is not None:
            current_task = get_task_as_formatted_string(current_task, include_plan=False, include_status=False, include_steps=False)
            await async_send_message(current_task, type="task", source="use_chat")

        await async_send_message(message, source="use_chat")
        duration = count_tokens(banter) / 3.0
        duration = int(duration)
        time.sleep(duration)

def start_connector(loop_dict):
    asyncio.run(start(loop_dict))

async def start(loop_dict):
    t = Twitch()
    t.twitch_connect(TWITCH_CHANNEL)
    await asyncio.gather(
        twitch_handle_loop(),
        twitch_handle_messages(),
    )