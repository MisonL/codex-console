from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_email_services_template_declares_edit_modal_titles():
    content = (ROOT / "templates" / "email_services.html").read_text(
        encoding="utf-8"
    )

    assert 'id="edit-custom-modal-title"' in content
    assert 'id="edit-outlook-modal-title"' in content


def test_email_services_script_registers_edit_modals_and_renders_edit_buttons():
    content = (ROOT / "static" / "js" / "email_services.js").read_text(
        encoding="utf-8"
    )

    assert "initializeEmailServiceModals()" in content
    assert "window.modal?.register?.bind(window.modal)" in content
    assert 'type="button" class="btn btn-secondary btn-sm" onclick="editOutlookService(' in content
    assert 'type="button" class="btn btn-secondary btn-sm" onclick="editCustomService(' in content


def test_settings_script_registers_edit_modals_and_keeps_email_service_edit_entry():
    content = (ROOT / "static" / "js" / "settings.js").read_text(
        encoding="utf-8"
    )

    assert "initializeSettingsEditModals()" in content
    assert "elements.addServiceModal," in content
    assert 'onclick="editEmailService(${service.id})"' in content
    assert "editingEmailServiceId = service.id" in content
    assert "service-modal-title" in content
    assert "openEmailServiceModal(\"edit\")" in content


def test_settings_template_declares_email_service_management_modal():
    content = (ROOT / "templates" / "settings.html").read_text(
        encoding="utf-8"
    )

    assert 'id="add-email-service-btn"' in content
    assert 'id="email-services-table"' in content
    assert 'id="add-service-modal"' in content
    assert 'id="service-modal-title"' in content
