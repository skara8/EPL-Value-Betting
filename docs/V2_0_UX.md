# V2.0 loading UX

Long-running refreshes no longer leave the user with only a static waiting message.

The Dashboard shows:

- a current stage;
- a short description of the operation in progress;
- percentage complete;
- elapsed time;
- an approximate ETA.

The progress callback is thread-safe: network worker threads write updates to a queue and the Tk main thread drains that queue. This avoids modifying Tk widgets directly from background threads.

The UI makes a distinction between **market results ready** and **all optional analysis complete**. Core market results can be viewed while EPL-specific player/xG/tactical enrichment is still running.
