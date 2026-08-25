# CPU utilisation note

A low total CPU percentage does not always mean the application is inefficient. Network/API stages are latency-bound and should remain low-CPU. The pure-Python probability/Poisson stage was different: it was genuinely CPU-bound but previously ran in a single Python interpreter, so a 12-logical-CPU PC could show roughly 8% total utilisation even while one core was saturated.

V2.2 uses multiple Windows worker processes for that CPU-bound stage and modest thread concurrency for independent network price feeds. This separates the correct optimisation for each bottleneck rather than trying to force 100% CPU during API waits.
