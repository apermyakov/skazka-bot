"""Smoke-test analyze_child_photo on Ksenia's Sofiechka photo + topic.
Should return a one-line Russian description of hair colour, length, eye colour.
"""
import asyncio
import base64
import sys

sys.path.insert(0, "/app")

PHOTO = "/app/media/_web_uploads/0448001900a74eacbfbe3c2da3915656.jpg"
TOPIC = (
    "Про Софиечку, которой исполнилось 3 годика и она готовится пойти первый раз в садик. "
    "Софиечка с голубыми глазами, длинные кудрявые золотистые волосы. "
    "Есть любимая собачка Риччи, щенок породы кинг чарльз спаниель."
)


async def main():
    from engine.image_generator import analyze_child_photo
    with open(PHOTO, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    desc = await analyze_child_photo(b64, topic=TOPIC)
    print("VLM описание:")
    print(f"  {desc}")


if __name__ == "__main__":
    asyncio.run(main())
