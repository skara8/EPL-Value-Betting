import v14_runtime_hook  # noqa: F401 - retain parser compatibility patch
import main_v19
from multileague_resilient import fetch_multileague_sources_resilient


main_v19.fetch_multileague_sources = fetch_multileague_sources_resilient


if __name__ == "__main__":
    main_v19.main()
