@app.route("/gemini-test")
def gemini_test():
    """Simple Gemini API connectivity test."""
    try:
        from google import genai

        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        model_name = (
            os.environ.get("GEMINI_MODEL", "gemini-flash-latest").strip()
            or "gemini-flash-latest"
        )

        if not api_key or api_key == "your_gemini_api_key_here":
            return jsonify({
                "status": "error",
                "error": "GEMINI_API_KEY is not configured",
            }), 500

        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model=model_name,
            contents="Reply with exactly: GEMINI_OK",
        )

        return jsonify({
            "status": "ok",
            "model": model_name,
            "response": response.text or "",
        })

    except Exception as e:
        logger.exception(f"Gemini connectivity test failed: {e}")

        return jsonify({
            "status": "error",
            "error": str(e),
        }), 500