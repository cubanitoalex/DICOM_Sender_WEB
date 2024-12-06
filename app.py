from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, make_response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.urls import url_parse
from models import db, User, ActivityLog
from forms import LoginForm, UserCreateForm, UserEditForm, ChangePasswordForm
import subprocess
import os
import shutil
import logging
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'tu_clave_secreta_muy_segura'  # Cambiar en producción
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///dcmsend.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inicializar extensiones
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Por favor inicia sesión para acceder a esta página.'

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@login_manager.user_loader
def load_user(id):
    return db.session.get(User, int(id))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('No tienes permiso para acceder a esta página.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def log_activity(action, details=None):
    log = ActivityLog(
        user_id=current_user.id,
        action=action,
        details=details,
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.check_password(form.password.data):
            flash('Usuario o contraseña incorrectos', 'error')
            return redirect(url_for('login'))
        if not user.active:
            flash('Tu cuenta está desactivada. Contacta al administrador.', 'error')
            return redirect(url_for('login'))
        
        login_user(user)
        user.last_login = datetime.utcnow()
        db.session.commit()
        log_activity('login', 'Inicio de sesión exitoso')
        
        next_page = request.args.get('next')
        if not next_page or url_parse(next_page).netloc != '':
            next_page = url_for('index')
        return redirect(next_page)
    
    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    log_activity('logout', 'Cierre de sesión')
    logout_user()
    return redirect(url_for('login'))

@app.route('/admin/users')
@login_required
@admin_required
def user_list():
    users = User.query.all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/create', methods=['GET', 'POST'])
@login_required
@admin_required
def user_create():
    form = UserCreateForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            role=form.role.data
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        log_activity('create_user', f'Usuario creado: {user.username}')
        flash('Usuario creado exitosamente', 'success')
        return redirect(url_for('user_list'))
    return render_template('admin/user_create.html', form=form)

@app.route('/admin/users/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def user_edit(id):
    user = User.query.get_or_404(id)
    form = UserEditForm(obj=user)
    if form.validate_on_submit():
        user.email = form.email.data
        user.active = form.active.data
        user.role = form.role.data
        db.session.commit()
        log_activity('edit_user', f'Usuario editado: {user.username}')
        flash('Usuario actualizado exitosamente', 'success')
        return redirect(url_for('user_list'))
    return render_template('admin/user_edit.html', form=form, user=user)

@app.route('/admin/users/change-password', methods=['POST'])
@login_required
@admin_required
def admin_change_user_password():
    try:
        user_id = request.form.get('user_id')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not all([user_id, new_password, confirm_password]):
            return jsonify({'status': 'error', 'message': 'Todos los campos son requeridos'}), 400
        
        if new_password != confirm_password:
            return jsonify({'status': 'error', 'message': 'Las contraseñas no coinciden'}), 400
        
        user = User.query.get_or_404(int(user_id))
        user.set_password(new_password)
        db.session.commit()
        
        log_activity('change_user_password', f'Cambio de contraseña para usuario: {user.username}')
        return jsonify({
            'status': 'success',
            'message': f'Contraseña actualizada exitosamente para {user.username}'
        })
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Error al cambiar contraseña: {str(e)}')
        return jsonify({'status': 'error', 'message': 'Error al cambiar la contraseña'}), 500

@app.route('/admin/logs')
@login_required
@admin_required
def view_logs():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    search_details = request.args.get('search_details', '')
    search_user = request.args.get('search_user', '')

    query = ActivityLog.query

    # Filtrar por usuario
    if search_user:
        query = query.join(ActivityLog.user).filter(User.username.ilike(f'%{search_user}%'))
    
    # Filtrar por detalles
    if search_details:
        query = query.filter(ActivityLog.details.ilike(f'%{search_details}%'))
    
    # Ordenar por fecha descendente
    query = query.order_by(ActivityLog.timestamp.desc())
    
    # Paginar resultados
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items
    
    # Obtener lista de usuarios para el selector
    users = User.query.order_by(User.username).all()
    
    return render_template('admin/logs.html', 
                         logs=logs, 
                         pagination=pagination,
                         search_details=search_details,
                         search_user=search_user,
                         users=users)

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
@admin_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash('Contraseña actual incorrecta', 'error')
            return redirect(url_for('change_password'))
        
        current_user.set_password(form.new_password.data)
        db.session.commit()
        log_activity('change_password', 'Cambio de contraseña exitoso')
        flash('Contraseña actualizada exitosamente', 'success')
        return redirect(url_for('index'))
    return render_template('change_password.html', form=form)

# Configurar la ruta para archivos estáticos
@app.route('/static/<path:filename>')
def static_files(filename):
    root_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(root_dir, filename)

@app.after_request
def add_header(response):
    # Prevenir caché en todas las respuestas
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

def analyze_dicom(file_path):
    dcmdump_path = '/usr/bin/dcmdump'
    dicom_data = {}
    
    # Lista de campos DICOM que queremos extraer
    fields = ['PatientName', 'PatientID', 'StudyDate', 'Modality', 'StudyDescription']
    
    try:
        for field in fields:
            command = [dcmdump_path, '+P', field, file_path]
            result = subprocess.run(command, capture_output=True, text=True)
            
            if result.stdout:
                # Extraer el valor entre corchetes
                start = result.stdout.find('[') + 1
                end = result.stdout.find(']')
                if start > 0 and end > start:
                    value = result.stdout[start:end].strip()
                    dicom_data[field] = value
                else:
                    dicom_data[field] = 'N/A'
            else:
                dicom_data[field] = 'N/A'
                
        return dicom_data
    except Exception as e:
        logger.error(f'Error analyzing DICOM: {str(e)}')
        return None

@app.route('/analyze', methods=['POST'])
@login_required
def analyze_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No se ha seleccionado ningún archivo'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No se ha seleccionado ningún archivo'}), 400

    try:
        # Crear directorio temporal
        temp_dir = os.path.join(os.path.dirname(__file__), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        # Guardar archivo temporalmente
        file_path = os.path.join(temp_dir, file.filename)
        file.save(file_path)
        
        # Analizar el archivo
        dicom_data = analyze_dicom(file_path)
        
        # Registrar la actividad
        log_activity('analyze_dicom', f'Archivo analizado: {file.filename}')
        
        # Limpiar
        os.remove(file_path)
        os.rmdir(temp_dir)
        
        if dicom_data:
            return jsonify(dicom_data)
        else:
            return jsonify({'error': 'Error al analizar el archivo DICOM'}), 500
            
    except Exception as e:
        logger.error(f'Error in analyze_file: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/', methods=['GET', 'POST'])
@login_required
def upload_file():
    if request.method == 'POST':
        if 'directory' not in request.files:
            flash('No se han seleccionado archivos', 'warning')
            return redirect(request.url)
        
        files = request.files.getlist('directory')
        if not files or files[0].filename == '':
            flash('No se han seleccionado archivos', 'warning')
            return redirect(request.url)

        upload_folder = os.path.join(os.path.dirname(__file__), 'uploads')
        os.makedirs(upload_folder, exist_ok=True)
        
        try:
            # Save all files
            for file in files:
                if file.filename:
                    file_path = os.path.join(upload_folder, file.filename)
                    file.save(file_path)
                    logger.info(f'Archivo guardado: {file_path}')

            # Run the dcmsend command
            dcmsend_path = '/usr/bin/dcmsend'  # Path for Linux
            if not os.path.exists(dcmsend_path):
                raise FileNotFoundError(f'No se encontró el ejecutable dcmsend en: {dcmsend_path}')

            command = [
                dcmsend_path,
                '-v',
                '-aet', 'SENDER',
                '-aec', 'DCM4CHEE',
                '192.168.1.3',
                '11112',
                '--scan-directories',
                '--recurse',
                upload_folder
            ]
            
            logger.info(f'Ejecutando comando: {" ".join(command)}')
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            logger.info(f'Salida del comando: {result.stdout}')
            
            flash('¡Archivos enviados exitosamente!', 'success')
            
        except FileNotFoundError as e:
            flash(f'Error: {str(e)}', 'danger')
            logger.error(f'Error: {str(e)}')
        except subprocess.CalledProcessError as e:
            flash(f'Error al enviar archivos: {e.stderr}', 'danger')
            logger.error(f'Error en el comando: {e.stderr}')
        except Exception as e:
            flash(f'Error inesperado: {str(e)}', 'danger')
            logger.error(f'Error inesperado: {str(e)}')
        finally:
            # Clean up uploaded files
            if os.path.exists(upload_folder):
                shutil.rmtree(upload_folder)
                logger.info('Carpeta de carga eliminada')
            
        return redirect(url_for('index'))
        
    return render_template('index.html')

# Crear las tablas de la base de datos
def init_db():
    with app.app_context():
        db.create_all()
        # Crear usuario admin por defecto si no existe
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                email='admin@example.com',
                role='admin',
                active=True
            )
            admin.set_password('admin123')  # Cambiar en producción
            db.session.add(admin)
            db.session.commit()

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5050)
