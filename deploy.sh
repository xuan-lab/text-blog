#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Configuration Variables (User may need to adjust these if defaults are not suitable) ---
PROJECT_NAME="text_blog_app" # Used for filenames, service names etc.
# Assumes script is run from the project root, so PROJECT_DIR will be the current directory
PROJECT_DIR=$(pwd)
PYTHON_ALIAS="python3"
PIP_ALIAS="pip3"
VENV_DIR="venv" # Name of the virtual environment directory

# Gunicorn settings
GUNICORN_WORKERS=3 # Adjust based on your server's CPU cores (e.g., 2 * cores + 1)
GUNICORN_HOST="127.0.0.1" # Gunicorn will bind to localhost
GUNICORN_PORT="8000"      # Gunicorn will listen on this port (internal)

# --- Main Deployment Steps ---

echo "🚀 Starting deployment of your Text Blog App..."
echo "----------------------------------------------------"

# 1. Get User Input
echo "Please provide the following information:"
read -p "Enter your domain name (e.g., yourblog.com, or server IP if no domain): " DOMAIN_NAME
read -p "Enter the Nginx listening port (e.g., 8080, 8888. Default is 80 if empty): " NGINX_LISTEN_PORT
read -s -p "Enter a strong SECRET_KEY for Flask sessions: " FLASK_SECRET_KEY
echo
read -s -p "Enter the ADMIN_PASSWORD_HASH for blog admin access: " ADMIN_PASSWORD_HASH_VALUE
echo
echo "----------------------------------------------------"

# Set default Nginx port if not provided
if [ -z "$NGINX_LISTEN_PORT" ]; then
    NGINX_LISTEN_PORT=80
    echo "Nginx will listen on default port 80."
else
    # Basic validation for port number (numeric)
    if ! [[ "$NGINX_LISTEN_PORT" =~ ^[0-9]+$ ]]; then
        echo "❌ Error: Nginx listening port must be a number."
        exit 1
    fi
    echo "Nginx will listen on port $NGINX_LISTEN_PORT."
fi


# Validate other input (basic check)
if [ -z "$DOMAIN_NAME" ] || [ -z "$FLASK_SECRET_KEY" ] || [ -z "$ADMIN_PASSWORD_HASH_VALUE" ]; then
    echo "❌ Error: Domain name, SECRET_KEY, and ADMIN_PASSWORD_HASH cannot be empty."
    exit 1
fi

# 2. System Update and Install Dependencies
echo "🔄 Updating system packages and installing dependencies (git, python3, python3-venv, nginx, supervisor)..."
sudo apt-get update
sudo apt-get install -y git $PYTHON_ALIAS $PYTHON_ALIAS-venv nginx supervisor
echo "----------------------------------------------------"

# 3. Setup Project Environment
echo "🐍 Setting up Python virtual environment..."
$PYTHON_ALIAS -m venv $VENV_DIR
echo "   Activating virtual environment..."
source $VENV_DIR/bin/activate

echo "   Installing Python dependencies from requirements.txt..."
$PIP_ALIAS install -r requirements.txt
echo "   Installing Gunicorn..."
$PIP_ALIAS install gunicorn

echo "   Deactivating virtual environment (Supervisor will manage it)..."
deactivate
echo "----------------------------------------------------"

# 4. Configure Supervisor for Gunicorn
echo "⚙️  Configuring Supervisor to manage Gunicorn..."
SUPERVISOR_CONF_FILE="/etc/supervisor/conf.d/${PROJECT_NAME}.conf"
# Determine the user to run Gunicorn as.
APP_USER=$(whoami)
if [ "$APP_USER" == "root" ] && [ -n "$SUDO_USER" ]; then
    APP_USER=$SUDO_USER
fi
echo "   Gunicorn will run as user: $APP_USER"

sudo bash -c "cat > $SUPERVISOR_CONF_FILE" << EOL
[program:${PROJECT_NAME}]
command=${PROJECT_DIR}/${VENV_DIR}/bin/gunicorn --workers ${GUNICORN_WORKERS} --bind ${GUNICORN_HOST}:${GUNICORN_PORT} app:app
directory=${PROJECT_DIR}
autostart=true
autorestart=true
stderr_logfile=/var/log/${PROJECT_NAME}_err.log
stdout_logfile=/var/log/${PROJECT_NAME}_out.log
user=${APP_USER}
environment=FLASK_APP="app.py",SECRET_KEY='${FLASK_SECRET_KEY}',ADMIN_PASSWORD_HASH='${ADMIN_PASSWORD_HASH_VALUE}'
EOL
echo "   Supervisor configuration created at: $SUPERVISOR_CONF_FILE"
echo "----------------------------------------------------"

# 5. Configure Nginx as Reverse Proxy
echo "🌐 Configuring Nginx as a reverse proxy..."
NGINX_SITE_CONF="/etc/nginx/sites-available/${PROJECT_NAME}"
sudo bash -c "cat > $NGINX_SITE_CONF" << EOL
server {
    listen ${NGINX_LISTEN_PORT}; # MODIFIED: Use user-defined port
    server_name ${DOMAIN_NAME};

    access_log /var/log/nginx/${PROJECT_NAME}_access.log;
    error_log /var/log/nginx/${PROJECT_NAME}_error.log;

    location /static {
        alias ${PROJECT_DIR}/static;
        expires 30d; # Cache static files
    }

    location / {
        proxy_pass http://${GUNICORN_HOST}:${GUNICORN_PORT};
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
    }
}
EOL
echo "   Nginx site configuration created at: $NGINX_SITE_CONF"

# Enable Nginx site
if [ -L "/etc/nginx/sites-enabled/${PROJECT_NAME}" ]; then
    echo "   Removing existing Nginx site symlink..."
    sudo rm /etc/nginx/sites-enabled/${PROJECT_NAME}
fi
echo "   Enabling Nginx site by creating symlink..."
sudo ln -s $NGINX_SITE_CONF /etc/nginx/sites-enabled/

# Test Nginx configuration
echo "   Testing Nginx configuration..."
sudo nginx -t
if [ $? -ne 0 ]; then
    echo "❌ Error: Nginx configuration test failed. Please check $NGINX_SITE_CONF and Nginx error logs."
    exit 1
fi

# Remove default Nginx site if it exists to avoid conflicts (optional, but good practice)
# Only remove if we are not trying to use port 80 for our app AND the default site is also on port 80
DEFAULT_NGINX_CONF="/etc/nginx/sites-enabled/default"
if [ "$NGINX_LISTEN_PORT" -ne 80 ] && [ -L "$DEFAULT_NGINX_CONF" ]; then
    # A more robust check would be to see if the default site actually listens on port 80
    echo "   Removing default Nginx site symlink (as it might conflict on port 80)..."
    sudo rm $DEFAULT_NGINX_CONF
elif [ "$NGINX_LISTEN_PORT" -eq 80 ] && [ -L "$DEFAULT_NGINX_CONF" ]; then
    echo "   Removing default Nginx site symlink (as it would conflict with our app on port 80)..."
    sudo rm $DEFAULT_NGINX_CONF
fi
echo "----------------------------------------------------"

# 6. Restart Services
echo "🔄 Reloading Supervisor and Nginx configurations..."
sudo supervisorctl reread
sudo supervisorctl update
echo "   Starting/Restarting Gunicorn process via Supervisor (${PROJECT_NAME})..."
sudo supervisorctl restart ${PROJECT_NAME}
echo "   Restarting Nginx..."
sudo systemctl restart nginx
echo "----------------------------------------------------"

# 7. Final Instructions
ACCESS_URL="http://${DOMAIN_NAME}"
if [ "$NGINX_LISTEN_PORT" -ne 80 ]; then
    ACCESS_URL="http://${DOMAIN_NAME}:${NGINX_LISTEN_PORT}"
fi

echo "✅ Deployment complete!"
echo "Your blog should be accessible at: ${ACCESS_URL}"
echo "If you used an IP address for the domain, use the IP in the URL above."
echo ""
echo "📝 Important Notes:"
echo "   - Nginx site configuration: ${NGINX_SITE_CONF}"
echo "   - Supervisor process configuration: ${SUPERVISOR_CONF_FILE}"
echo "   - Gunicorn application logs: /var/log/${PROJECT_NAME}_out.log (stdout) and /var/log/${PROJECT_NAME}_err.log (stderr)"
echo "   - Nginx logs: /var/log/nginx/${PROJECT_NAME}_access.log and /var/log/nginx/${PROJECT_NAME}_error.log"
echo "   - Ensure your domain's DNS records point to this server's IP address if you used a domain name."
echo "   - Ensure your server's firewall allows traffic on port ${NGINX_LISTEN_PORT}."
if [ "$NGINX_LISTEN_PORT" -eq 80 ]; then
    echo "   - For HTTPS (recommended for production), configure Certbot (Let's Encrypt) after this setup:"
    echo "     Example for Certbot with Nginx: sudo apt install certbot python3-certbot-nginx; sudo certbot --nginx -d ${DOMAIN_NAME}"
else
    echo "   - For HTTPS with a non-standard port, Certbot configuration might be more complex. You might need to temporarily switch to port 80 for validation or use DNS validation if your provider supports it."
fi
echo "----------------------------------------------------"

exit 0
