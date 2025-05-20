import os
import re
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, abort, flash
from werkzeug.security import check_password_hash, generate_password_hash
import markdown
import frontmatter
from flask_wtf import CSRFProtect, FlaskForm
from wtforms import StringField, PasswordField, TextAreaField, SubmitField, DateField
from wtforms.validators import DataRequired, Length, Optional

app = Flask(__name__)
# IMPORTANT: Change to a static, complex key for production and consistent development sessions.
# Using os.urandom(24) will invalidate sessions on each app restart.
app.config['SECRET_KEY'] = 'dev_secret_key_please_change_for_prod' 
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# For production, if using HTTPS, uncomment the next line:
# app.config['SESSION_COOKIE_SECURE'] = True
csrf = CSRFProtect(app)

# --- Configuration ---
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD_HASH = generate_password_hash('textblog') # Password set to textblog
POSTS_DIR = os.path.join(os.path.dirname(__file__), 'posts')
os.makedirs(POSTS_DIR, exist_ok=True)

# --- Forms (Flask-WTF) ---
class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class PostForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired(), Length(min=1, max=200)])
    slug = StringField('Custom Slug (Optional)', 
                     validators=[Optional(), 
                                 Length(max=200)])
    content = TextAreaField('Content (Markdown)', validators=[DataRequired()])
    submit = SubmitField('Save Post')

class DeleteForm(FlaskForm):
    submit = SubmitField('Delete')

# --- Helper Functions ---
def get_all_posts():
    """Reads all markdown posts, parses frontmatter, and returns them sorted by date."""
    posts = []
    for filename in os.listdir(POSTS_DIR):
        if filename.endswith('.md'):
            filepath = os.path.join(POSTS_DIR, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    post_fm = frontmatter.load(f)
                    post_content = post_fm.content
                    # Ensure date is a datetime object for sorting
                    date_meta = post_fm.metadata.get('date')
                    if isinstance(date_meta, str):
                        try:
                            post_fm.metadata['date'] = datetime.strptime(date_meta, '%Y-%m-%d')
                        except ValueError:
                            post_fm.metadata['date'] = datetime.now() # Fallback
                    elif not isinstance(date_meta, datetime):
                         post_fm.metadata['date'] = datetime.now() # Fallback

                    posts.append({
                        'slug': filename[:-3],
                        'title': post_fm.metadata.get('title', 'Untitled Post'),
                        'date': post_fm.metadata['date'],
                        'content_md': post_content,
                        'content_html': markdown.markdown(post_content, extensions=['fenced_code', 'tables'])
                    })
            except Exception as e:
                print(f"Error processing file {filename}: {e}") 
                continue 
    posts.sort(key=lambda p: p['date'], reverse=True)
    return posts

def get_post_by_slug(slug):
    """Fetches a single post by its slug."""
    filepath = os.path.join(POSTS_DIR, slug + '.md')
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                post_fm = frontmatter.load(f)
                post_content = post_fm.content
                date_obj = post_fm.metadata.get('date')
                if isinstance(date_obj, str):
                    try:
                        date_obj = datetime.strptime(date_obj, '%Y-%m-%d')
                    except ValueError:
                        date_obj = datetime.now() 
                elif not isinstance(date_obj, datetime):
                    date_obj = datetime.now() 

                return {
                    'slug': slug,
                    'title': post_fm.metadata.get('title', 'Untitled Post'),
                    'date': date_obj,
                    'content_md': post_content,
                    'content_html': markdown.markdown(post_content, extensions=['fenced_code', 'tables'])
                }
        except Exception as e:
            print(f"Error processing file {slug}.md: {e}")
            return None
    return None

def slugify(text):
    """Generates a URL-friendly slug from text."""
    text = re.sub(r'[^\w\s-]', '', text).strip().lower()
    text = re.sub(r'[\s_-]+', '-', text)
    return text

# --- Frontend Routes ---
@app.route('/')
def index():
    posts = get_all_posts()
    return render_template('index.html', posts=posts)

@app.route('/post/<slug>')
def post(slug):
    post = get_post_by_slug(slug)
    if post:
        return render_template('post.html', post=post)
    abort(404)

# --- Admin Routes ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session['admin_logged_in'] = True
            flash('Logged in successfully.', 'success')
            next_url = request.args.get('next')
            return redirect(next_url or url_for('admin_dashboard'))
        else:
            flash('Invalid username or password.', 'error')
    return render_template('admin_login.html', form=form)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('admin_login'))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin')
@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    posts = get_all_posts()
    delete_form = DeleteForm()
    return render_template('admin_dashboard.html', posts=posts, delete_form=delete_form)

@app.route('/admin/post/new', methods=['GET', 'POST'])
@login_required
def admin_new_post():
    form = PostForm()
    if form.validate_on_submit():
        title = form.title.data
        content = form.content.data
        custom_slug = form.slug.data

        if custom_slug:
            slug = slugify(custom_slug)
        else:
            slug = slugify(title)

        if not slug: 
            slug = "untitled-post-" + datetime.now().strftime("%Y%m%d%H%M%S")

        filename = slug + '.md'
        filepath = os.path.join(POSTS_DIR, filename)

        if os.path.exists(filepath):
            flash(f'Post with slug "{slug}" already exists. Choose a different title or custom slug.', 'error')
            return render_template('admin_edit_post.html', form=form, is_edit=False, post_slug=None)

        post_data = {
            'title': title,
            'date': datetime.now().strftime('%Y-%m-%d')
        }
        fm_post = frontmatter.Post(content, **post_data)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                serialized_post = frontmatter.dumps(fm_post)
                f.write(serialized_post)
            flash(f'Post "{title}" created successfully!', 'success')
            return redirect(url_for('admin_dashboard'))
        except Exception as e:
            flash(f'Error saving post: {e}', 'error')
    return render_template('admin_edit_post.html', form=form, is_edit=False, post_slug=None)

@app.route('/admin/post/edit/<slug_param>', methods=['GET', 'POST'])
@login_required
def admin_edit_post(slug_param):
    post_to_edit = get_post_by_slug(slug_param)
    if not post_to_edit:
        abort(404)

    form = PostForm(obj=post_to_edit if request.method == 'GET' else None)
    if request.method == 'GET':
        form.content.data = post_to_edit['content_md'] # Necessary due to key mismatch (content vs content_md)

    if form.validate_on_submit():
        title = form.title.data
        content = form.content.data

        filepath = os.path.join(POSTS_DIR, slug_param + '.md')
        post_data = {
            'title': title,
            'date': post_to_edit['date'].strftime('%Y-%m-%d') if isinstance(post_to_edit['date'], datetime) else post_to_edit['date']
        }
        fm_post = frontmatter.Post(content, **post_data)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                serialized_post = frontmatter.dumps(fm_post)
                f.write(serialized_post)
            flash(f'Post "{title}" updated successfully!', 'success')
            return redirect(url_for('admin_dashboard'))
        except Exception as e:
            flash(f'Error saving post: {e}', 'error')

    return render_template('admin_edit_post.html', form=form, is_edit=True, post_slug=slug_param)

@app.route('/admin/post/delete/<slug_param>', methods=['POST'])
@login_required
def admin_delete_post(slug_param):
    form = DeleteForm()
    if form.validate_on_submit():
        filepath = os.path.join(POSTS_DIR, slug_param + '.md')
        post_title = slug_param
        temp_post = get_post_by_slug(slug_param)
        if temp_post:
            post_title = temp_post.get('title', slug_param)

        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                flash(f'Post "{post_title}" deleted successfully.', 'success')
            except Exception as e:
                flash(f'Error deleting post \'{post_title}\': {e}', 'error')
        else:
            flash(f'Post "{post_title}" not found for deletion.', 'warning')
    else:
        flash('Error deleting post. Invalid request.', 'error')
    return redirect(url_for('admin_dashboard'))

# --- Error Handlers ---
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

@app.context_processor
def inject_current_year():
    return {'current_year': datetime.utcnow().year}

if __name__ == '__main__':
    app.run(debug=True)
