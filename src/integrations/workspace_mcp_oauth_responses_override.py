from typing import Optional

from fastapi.responses import HTMLResponse


def create_error_response(error_message: str, status_code: int = 400) -> HTMLResponse:
    content = f"""
        <html>
        <head><title>Authentication Error</title></head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; text-align: center;">
            <h2 style="color: #d32f2f;">Authentication Error</h2>
            <p>{error_message}</p>
            <p>Please ensure you grant the requested permissions. You can close this window and try again.</p>
            <script>setTimeout(function() {{ window.close(); }}, 10000);</script>
        </body>
        </html>
    """
    return HTMLResponse(content=content, status_code=status_code)


def create_success_response(verified_user_id: Optional[str] = None) -> HTMLResponse:
    user_display = verified_user_id if verified_user_id else "Google User"

    content = f"""<html>
<head>
    <title>Authentication Successful</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg,#0f172a,#1e293b,#334155);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #1a1a1a;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}

        .container {{
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            padding: 60px;
            border-radius: 20px;
            box-shadow: 0 30px 60px rgba(0, 0, 0, 0.12);
            text-align: center;
            max-width: 480px;
            width: 90%;
            transform: translateY(-20px);
            animation: slideUp 0.6s ease-out;
        }}

        @keyframes slideUp {{
            from {{
                opacity: 0;
                transform: translateY(0);
            }}
            to {{
                opacity: 1;
                transform: translateY(-20px);
            }}
        }}

        .icon {{
            width: 80px;
            height: 80px;
            margin: 0 auto 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 40px;
            color: white;
            animation: pulse 2s ease-in-out infinite;
        }}

        @keyframes pulse {{
            0%, 100% {{
                transform: scale(1);
            }}
            50% {{
                transform: scale(1.05);
            }}
        }}

        h1 {{
            font-size: 28px;
            font-weight: 600;
            margin-bottom: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}

        .message {{
            font-size: 16px;
            line-height: 1.6;
            color: #4a5568;
            margin-bottom: 20px;
        }}

        .user-id {{
            font-weight: 600;
            color: #667eea;
            padding: 4px 12px;
            background: rgba(102, 126, 234, 0.1);
            border-radius: 6px;
            display: inline-block;
            margin: 0 4px;
        }}

        .button {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 16px 40px;
            border: none;
            border-radius: 30px;
            font-size: 16px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.3s ease;
            margin-top: 30px;
            display: inline-block;
            text-decoration: none;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
        }}

        .button:hover {{
            transform: translateY(-2px);
            box-shadow: 0 7px 20px rgba(102, 126, 234, 0.4);
        }}

        .button:active {{
            transform: translateY(0);
        }}

        .auto-close {{
            font-size: 13px;
            color: #a0aec0;
            margin-top: 30px;
            opacity: 0.8;
        }}
    </style>
    <script>
        document.addEventListener("DOMContentLoaded", function() {{
            const closeButton = document.getElementById("close-button");
            const countdownValue = document.getElementById("countdown-value");
            const closeStatus = document.getElementById("close-status");
            let remainingSeconds = 10;
            let closeAttempted = false;

            function updateCountdown() {{
                countdownValue.textContent = String(remainingSeconds);
            }}

            function showManualCloseFallback() {{
                closeStatus.textContent = "Your browser prevented automatic closing. You can safely close this tab manually.";
            }}

            function attemptClose() {{
                window.open("", "_self");
                window.close();
            }}

            function handleCloseRequest() {{
                if (closeAttempted) {{
                    return;
                }}
                closeAttempted = true;
                attemptClose();
                window.setTimeout(function() {{
                    if (document.visibilityState !== "hidden") {{
                        showManualCloseFallback();
                    }}
                }}, 300);
            }}

            closeButton.addEventListener("click", function() {{
                handleCloseRequest();
            }});

            updateCountdown();
            const intervalId = window.setInterval(function() {{
                remainingSeconds -= 1;
                if (remainingSeconds <= 0) {{
                    remainingSeconds = 0;
                    updateCountdown();
                    window.clearInterval(intervalId);
                    handleCloseRequest();
                    return;
                }}
                updateCountdown();
            }}, 1000);
        }});
    </script>
</head>
<body>
    <div class="container">
        <div class="icon">✓</div>
        <h1>Authentication Successful</h1>
        <div class="message">
            You've been authenticated as <span class="user-id">{user_display}</span>
        </div>
        <div class="message">
            Your credentials have been securely saved. You can now close this window and retry your original command.
        </div>
        <button class="button" id="close-button" type="button">Close Window</button>
        <div class="auto-close" id="close-status">This window will close automatically in <span id="countdown-value">10</span> seconds</div>
    </div>
</body>
</html>"""
    return HTMLResponse(content=content)


def create_server_error_response(error_detail: str) -> HTMLResponse:
    content = f"""
        <html>
        <head><title>Authentication Processing Error</title></head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; text-align: center;">
            <h2 style="color: #d32f2f;">Authentication Processing Error</h2>
            <p>An unexpected error occurred while processing your authentication: {error_detail}</p>
            <p>Please try again. You can close this window.</p>
            <script>setTimeout(function() {{ window.close(); }}, 10000);</script>
        </body>
        </html>
    """
    return HTMLResponse(content=content, status_code=500)
