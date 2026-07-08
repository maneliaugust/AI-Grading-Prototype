Windows PowerShell
Copyright (C) Microsoft Corporation. All rights reserved.

Install the latest PowerShell for new features and improvements! https://aka.ms/PSWindows

PS C:\Users\manel> cd "C:\Users\manel\OneDrive\Documents\AI_Grading_Prototype\AI-Grading-Prototype"
PS C:\Users\manel\OneDrive\Documents\AI_Grading_Prototype\AI-Grading-Prototype> .\.venv\Scripts\Activate.ps1
(.venv) PS C:\Users\manel\OneDrive\Documents\AI_Grading_Prototype\AI-Grading-Prototype> python moodle_quiz_producer.py --course-id 2 --quiz-id 1 --course-name "Business Strategy" --quiz-name "Business Strategy Fundamentals Quiz" --subject-area "Business Strategy" --grading-guide grading_guide_quiz.json --userids 4 5 6 7 --host localhost
2026-07-08 17:00:22 [INFO] Processing attempt 12 for userid=4 (u12345678)
2026-07-08 17:00:22 [INFO] Extracted 2 essay response(s) from attempt 12
2026-07-08 17:00:22 [INFO] Objective score for attempt 12: 4.0
2026-07-08 17:00:22 [INFO]   [+] Job queued: u12345678 | Q11 | attempt=12
2026-07-08 17:00:22 [INFO]   [+] Job queued: u12345678 | Q12 | attempt=12
2026-07-08 17:00:22 [INFO] Processing attempt 13 for userid=5 (u98765432)
2026-07-08 17:00:23 [INFO] Extracted 2 essay response(s) from attempt 13
2026-07-08 17:00:23 [INFO] Objective score for attempt 13: 6.0
2026-07-08 17:00:23 [INFO]   [+] Job queued: u98765432 | Q11 | attempt=13
2026-07-08 17:00:23 [INFO]   [+] Job queued: u98765432 | Q12 | attempt=13
2026-07-08 17:00:23 [INFO] Processing attempt 10 for userid=6 (u24688642)
2026-07-08 17:00:23 [INFO] Extracted 2 essay response(s) from attempt 10
2026-07-08 17:00:23 [INFO] Objective score for attempt 10: 9.0
2026-07-08 17:00:23 [INFO]   [+] Job queued: u24688642 | Q11 | attempt=10
2026-07-08 17:00:23 [INFO]   [+] Job queued: u24688642 | Q12 | attempt=10
2026-07-08 17:00:24 [WARNING] Skipping userid=7 — no idnumber set on this Moodle account.
2026-07-08 17:00:24 [INFO] Pika version 1.4.1 connecting to ('::1', 5672, 0, 0)
2026-07-08 17:00:24 [INFO] Socket connected: <socket.socket fd=1196, family=23, type=1, proto=6, laddr=('::1', 57045, 0, 0), raddr=('::1', 5672, 0,0)>
2026-07-08 17:00:24 [INFO] Streaming transport linked up: (<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x000001A3BF42DA90>, _StreamingProtocolShim: <SelectConnection PROTOCOL transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x000001A3BF42DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>).
2026-07-08 17:00:24 [INFO] AMQPConnector - reporting success: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x000001A3BF42DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>
2026-07-08 17:00:24 [INFO] AMQPConnectionWorkflow - reporting success: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x000001A3BF42DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>
2026-07-08 17:00:24 [INFO] Connection workflow succeeded: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x000001A3BF42DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>
2026-07-08 17:00:24 [INFO] Created channel=1
2026-07-08 17:00:24 [INFO] [->] Queued: u12345678 | Business Strategy Fundamentals Quiz — Q11
2026-07-08 17:00:24 [INFO] [->] Queued: u12345678 | Business Strategy Fundamentals Quiz — Q12
2026-07-08 17:00:24 [INFO] [->] Queued: u98765432 | Business Strategy Fundamentals Quiz — Q11
2026-07-08 17:00:24 [INFO] [->] Queued: u98765432 | Business Strategy Fundamentals Quiz — Q12
2026-07-08 17:00:24 [INFO] [->] Queued: u24688642 | Business Strategy Fundamentals Quiz — Q11
2026-07-08 17:00:24 [INFO] [->] Queued: u24688642 | Business Strategy Fundamentals Quiz — Q12
2026-07-08 17:00:24 [INFO] [OK] 6 grading job(s) submitted to 'grading_jobs' queue.
2026-07-08 17:00:24 [INFO] Closing connection (200): Normal shutdown
2026-07-08 17:00:24 [INFO] Closing channel (200): 'Normal shutdown' on <Channel number=1 OPEN conn=<SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x000001A3BF42DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>>
2026-07-08 17:00:24 [INFO] Received <Channel.CloseOk> on <Channel number=1 CLOSING conn=<SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x000001A3BF42DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>>
2026-07-08 17:00:24 [INFO] Closing connection (200): 'Normal shutdown'
2026-07-08 17:00:24 [INFO] Aborting transport connection: state=1; <socket.socket fd=1196, family=23, type=1, proto=6, laddr=('::1', 57045, 0, 0), raddr=('::1', 5672, 0, 0)>
2026-07-08 17:00:24 [INFO] _AsyncTransportBase._initate_abort(): Initiating abrupt asynchronous transport shutdown: state=1; error=None; <socket.socket fd=1196, family=23, type=1, proto=6, laddr=('::1', 57045, 0, 0), raddr=('::1', 5672, 0, 0)>
2026-07-08 17:00:24 [INFO] Deactivating transport: state=1; <socket.socket fd=1196, family=23, type=1, proto=6, laddr=('::1', 57045, 0, 0), raddr=('::1', 5672, 0, 0)>
2026-07-08 17:00:24 [INFO] AMQP stack terminated, failed to connect, or aborted: opened=True, error-arg=None; pending-error=ConnectionClosedByClient: (200) 'Normal shutdown'
2026-07-08 17:00:24 [INFO] Stack terminated due to ConnectionClosedByClient: (200) 'Normal shutdown'
2026-07-08 17:00:24 [INFO] Closing transport socket and unlinking: state=3; <socket.socket fd=1196, family=23, type=1, proto=6, laddr=('::1', 57045, 0, 0), raddr=('::1', 5672, 0, 0)>
2026-07-08 17:00:24 [INFO] User-initiated close: result=BlockingConnection__OnClosedArgs(connection=<SelectConnection CLOSED transport=None params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>, error=ConnectionClosedByClient: (200) 'Normal shutdown')
(.venv) PS C:\Users\manel\OneDrive\Documents\AI_Grading_Prototype\AI-Grading-Prototype> python moodle_quiz_producer.py --course-id 2 --quiz-id 1 --course-name "Business Strategy" --quiz-name "Business Strategy Fundamentals Quiz" --subject-area "Business Strategy" --grading-guide grading_guide_quiz.json --userids 7 --host localhost
2026-07-08 17:05:01 [WARNING] Skipping userid=7 — no idnumber set on this Moodle account.
2026-07-08 17:05:01 [WARNING] No jobs to publish.
(.venv) PS C:\Users\manel\OneDrive\Documents\AI_Grading_Prototype\AI-Grading-Prototype> python moodle_quiz_producer.py --course-id 2 --quiz-id 1 --course-name "Business Strategy" --quiz-name "Business Strategy Fundamentals Quiz" --subject-area "Business Strategy" --grading-guide grading_guide_quiz.json --userids 8 --host localhost
2026-07-08 17:06:11 [INFO] Processing attempt 11 for userid=8 (u78191898)
2026-07-08 17:06:12 [INFO] Extracted 2 essay response(s) from attempt 11
2026-07-08 17:06:12 [INFO] Objective score for attempt 11: 14.0
2026-07-08 17:06:12 [INFO]   [+] Job queued: u78191898 | Q11 | attempt=11
2026-07-08 17:06:12 [INFO]   [+] Job queued: u78191898 | Q12 | attempt=11
2026-07-08 17:06:12 [INFO] Pika version 1.4.1 connecting to ('::1', 5672, 0, 0)
2026-07-08 17:06:12 [INFO] Socket connected: <socket.socket fd=1180, family=23, type=1, proto=6, laddr=('::1', 52422, 0, 0), raddr=('::1', 5672, 0,0)>
2026-07-08 17:06:12 [INFO] Streaming transport linked up: (<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x00000232E5C9DA90>, _StreamingProtocolShim: <SelectConnection PROTOCOL transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x00000232E5C9DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>).
2026-07-08 17:06:12 [INFO] AMQPConnector - reporting success: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x00000232E5C9DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>
2026-07-08 17:06:12 [INFO] AMQPConnectionWorkflow - reporting success: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x00000232E5C9DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>
2026-07-08 17:06:12 [INFO] Connection workflow succeeded: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x00000232E5C9DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>
2026-07-08 17:06:12 [INFO] Created channel=1
2026-07-08 17:06:12 [INFO] [->] Queued: u78191898 | Business Strategy Fundamentals Quiz — Q11
2026-07-08 17:06:12 [INFO] [->] Queued: u78191898 | Business Strategy Fundamentals Quiz — Q12
2026-07-08 17:06:12 [INFO] [OK] 2 grading job(s) submitted to 'grading_jobs' queue.
2026-07-08 17:06:12 [INFO] Closing connection (200): Normal shutdown
2026-07-08 17:06:12 [INFO] Closing channel (200): 'Normal shutdown' on <Channel number=1 OPEN conn=<SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x00000232E5C9DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>>
2026-07-08 17:06:12 [INFO] Received <Channel.CloseOk> on <Channel number=1 CLOSING conn=<SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x00000232E5C9DA90> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>>
2026-07-08 17:06:12 [INFO] Closing connection (200): 'Normal shutdown'
2026-07-08 17:06:12 [INFO] Aborting transport connection: state=1; <socket.socket fd=1180, family=23, type=1, proto=6, laddr=('::1', 52422, 0, 0), raddr=('::1', 5672, 0, 0)>
2026-07-08 17:06:12 [INFO] _AsyncTransportBase._initate_abort(): Initiating abrupt asynchronous transport shutdown: state=1; error=None; <socket.socket fd=1180, family=23, type=1, proto=6, laddr=('::1', 52422, 0, 0), raddr=('::1', 5672, 0, 0)>
2026-07-08 17:06:12 [INFO] Deactivating transport: state=1; <socket.socket fd=1180, family=23, type=1, proto=6, laddr=('::1', 52422, 0, 0), raddr=('::1', 5672, 0, 0)>
2026-07-08 17:06:12 [INFO] AMQP stack terminated, failed to connect, or aborted: opened=True, error-arg=None; pending-error=ConnectionClosedByClient: (200) 'Normal shutdown'
2026-07-08 17:06:12 [INFO] Stack terminated due to ConnectionClosedByClient: (200) 'Normal shutdown'
2026-07-08 17:06:12 [INFO] Closing transport socket and unlinking: state=3; <socket.socket fd=1180, family=23, type=1, proto=6, laddr=('::1', 52422, 0, 0), raddr=('::1', 5672, 0, 0)>
2026-07-08 17:06:12 [INFO] User-initiated close: result=BlockingConnection__OnClosedArgs(connection=<SelectConnection CLOSED transport=None params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>, error=ConnectionClosedByClient: (200) 'Normal shutdown')
(.venv) PS C:\Users\manel\OneDrive\Documents\AI_Grading_Prototype\AI-Grading-Prototype>
