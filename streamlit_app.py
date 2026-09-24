from hip_id_agent.runtime_env import configure_utf8_stdio

configure_utf8_stdio()

from hip_id_agent.streamlit_dashboard import main


if __name__ == "__main__":
    main()
