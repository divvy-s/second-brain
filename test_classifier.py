import sys
sys.stdout.reconfigure(encoding="utf-8")
from api.dependencies import build_services

svc = build_services()

tests = [
    "message naman that theres a meeting at 5",
    "buy milk tonight",
    "schedule meeting with john tomorrow at 3pm",
    "email sarah the quarterly report",
    "remind me to call the dentist",
    "whatsapp mom happy birthday",
]

for text in tests:
    intent = svc.classifier.classify(text)
    print(f"INPUT:  {text}")
    print(f"INTENT: {intent.intent} -> {intent.plugin}")
    print(f"FIELDS: {intent.fields}")
    print(f"REASON: {intent.reasoning}")
    print(f"CONF:   {intent.confidence}")
    print("---")
