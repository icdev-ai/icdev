# CUI // SP-CTI
# MSK Kafka streaming consumer — replaces NiFi real-time flow.
# Deploy as AWS Glue Streaming job or Lambda with MSK trigger.
import json
import os
from kafka import KafkaConsumer

MSK_BROKERS = os.environ["MSK_BOOTSTRAP_SERVERS"].split(",")
TOPIC        = os.environ.get("KAFKA_TOPIC", "analytics-events")
GROUP_ID     = os.environ.get("KAFKA_GROUP_ID", "analytics-glue-consumer")

consumer = KafkaConsumer(
    TOPIC,
    bootstrap_servers=MSK_BROKERS,
    group_id=GROUP_ID,
    auto_offset_reset="earliest",
    enable_auto_commit=False,
    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    security_protocol="SASL_SSL",
    sasl_mechanism="AWS_MSK_IAM",
)

def process_message(msg: dict) -> None:
    """Business logic — replace with actual transformation."""
    print(f"Processing: {msg}")

for message in consumer:
    process_message(message.value)
    consumer.commit()
