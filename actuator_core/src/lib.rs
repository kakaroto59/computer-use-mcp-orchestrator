use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::io::Cursor;
use base64::prelude::*;
use enigo::{
    Axis, Button, Coordinate, Direction, Enigo, Key, Keyboard, Mouse, Settings
};
use image::ImageFormat;
use windows::Win32::Graphics::Gdi::{
    BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDC, GetDIBits,
    ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, RGBQUAD, SRCCOPY,
};
use windows::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};

#[link(name = "user32")]
extern "system" {
    fn OpenInputDesktop(dwflags: u32, finherit: i32, dwdesiredaccess: u32) -> isize;
    fn SetThreadDesktop(hdesktop: isize) -> i32;
}

pub fn ensure_input_desktop() {
    unsafe {
        let hdesk = OpenInputDesktop(0, 0, 0x01FF);
        if hdesk != 0 {
            SetThreadDesktop(hdesk);
        }
    }
}

fn parse_key(key_name: &str) -> Result<Key, String> {
    match key_name.to_lowercase().as_str() {
        "return" | "enter" => Ok(Key::Return),
        "tab" => Ok(Key::Tab),
        "space" => Ok(Key::Space),
        "backspace" => Ok(Key::Backspace),
        "escape" | "esc" => Ok(Key::Escape),
        "control" | "ctrl" => Ok(Key::Control),
        "alt" => Ok(Key::Alt),
        "shift" => Ok(Key::Shift),
        "meta" | "win" | "super" => Ok(Key::Meta),
        "up" => Ok(Key::UpArrow),
        "down" => Ok(Key::DownArrow),
        "left" => Ok(Key::LeftArrow),
        "right" => Ok(Key::RightArrow),
        s if s.chars().count() == 1 => Ok(Key::Unicode(s.chars().next().unwrap())),
        _ => Err(format!("Unknown key: {}", key_name)),
    }
}

fn capture_screen_gdi() -> Result<image::RgbaImage, String> {
    ensure_input_desktop();
    unsafe {
        let width = GetSystemMetrics(SM_CXSCREEN);
        let height = GetSystemMetrics(SM_CYSCREEN);

        let hdc_screen = GetDC(None);
        if hdc_screen.is_invalid() {
            return Err("Failed to get screen DC".into());
        }

        let hdc_mem = CreateCompatibleDC(Some(hdc_screen));
        if hdc_mem.is_invalid() {
            let _ = ReleaseDC(None, hdc_screen);
            return Err("Failed to create compatible DC".into());
        }

        let h_bitmap = CreateCompatibleBitmap(hdc_screen, width, height);
        if h_bitmap.is_invalid() {
            let _ = DeleteDC(hdc_mem);
            let _ = ReleaseDC(None, hdc_screen);
            return Err("Failed to create compatible bitmap".into());
        }

        let old_bitmap = SelectObject(hdc_mem, h_bitmap.into());

        let blt_res = BitBlt(hdc_mem, 0, 0, width, height, Some(hdc_screen), 0, 0, SRCCOPY);
        if let Err(e) = blt_res {
            SelectObject(hdc_mem, old_bitmap);
            let _ = DeleteObject(h_bitmap.into());
            let _ = DeleteDC(hdc_mem);
            let _ = ReleaseDC(None, hdc_screen);
            return Err(format!("BitBlt failed: {:?}", e));
        }

        let mut bmi = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: width,
                biHeight: -height, // top-down bitmap
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                biSizeImage: (width * height * 4) as u32,
                biXPelsPerMeter: 0,
                biYPelsPerMeter: 0,
                biClrUsed: 0,
                biClrImportant: 0,
            },
            bmiColors: [RGBQUAD::default(); 1],
        };

        let mut raw_pixels = vec![0u8; (width * height * 4) as usize];
        let lines = GetDIBits(
            hdc_mem,
            h_bitmap,
            0,
            height as u32,
            Some(raw_pixels.as_mut_ptr() as *mut _),
            &mut bmi,
            DIB_RGB_COLORS,
        );

        SelectObject(hdc_mem, old_bitmap);
        let _ = DeleteObject(h_bitmap.into());
        let _ = DeleteDC(hdc_mem);
        let _ = ReleaseDC(None, hdc_screen);

        if lines == 0 {
            return Err("GetDIBits failed".into());
        }

        // Convert BGRA to RGBA
        for chunk in raw_pixels.chunks_exact_mut(4) {
            chunk.swap(0, 2);
            chunk[3] = 255;
        }

        image::RgbaImage::from_raw(width as u32, height as u32, raw_pixels)
            .ok_or_else(|| "Failed to construct RgbaImage from raw buffer".into())
    }
}

#[pyfunction]
#[pyo3(signature = (format = "jpeg", quality = 80))]
fn get_screenshot(format: &str, quality: u8) -> PyResult<String> {
    let img = capture_screen_gdi().map_err(PyRuntimeError::new_err)?;
    
    let mut buffer = Cursor::new(Vec::new());
    if format.eq_ignore_ascii_case("jpeg") || format.eq_ignore_ascii_case("jpg") {
        let mut encoder = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut buffer, quality);
        encoder
            .encode_image(&img)
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
    } else {
        img.write_to(&mut buffer, ImageFormat::Png)
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
    }

    Ok(BASE64_STANDARD.encode(buffer.into_inner()))
}

#[pyfunction]
fn get_screen_dimensions() -> PyResult<(u32, u32)> {
    ensure_input_desktop();
    unsafe {
        let width = GetSystemMetrics(SM_CXSCREEN);
        let height = GetSystemMetrics(SM_CYSCREEN);
        Ok((width as u32, height as u32))
    }
}

#[pyfunction]
#[pyo3(signature = (x, y, button = "left"))]
fn click_at(x: i32, y: i32, button: &str) -> PyResult<()> {
    ensure_input_desktop();
    let mut enigo = Enigo::new(&Settings::default())
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to init Enigo: {:?}", e)))?;

    enigo
        .move_mouse(x, y, Coordinate::Abs)
        .map_err(|e| PyRuntimeError::new_err(format!("Mouse move error: {:?}", e)))?;

    let btn = match button.to_lowercase().as_str() {
        "right" => Button::Right,
        "middle" => Button::Middle,
        _ => Button::Left,
    };

    enigo
        .button(btn, Direction::Click)
        .map_err(|e| PyRuntimeError::new_err(format!("Mouse click error: {:?}", e)))?;

    Ok(())
}

#[pyfunction]
fn mouse_move(x: i32, y: i32) -> PyResult<()> {
    ensure_input_desktop();
    let mut enigo = Enigo::new(&Settings::default())
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to init Enigo: {:?}", e)))?;

    enigo
        .move_mouse(x, y, Coordinate::Abs)
        .map_err(|e| PyRuntimeError::new_err(format!("Mouse move error: {:?}", e)))?;

    Ok(())
}

#[pyfunction]
fn type_text(text: &str) -> PyResult<()> {
    ensure_input_desktop();
    let mut enigo = Enigo::new(&Settings::default())
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to init Enigo: {:?}", e)))?;

    enigo
        .text(text)
        .map_err(|e| PyRuntimeError::new_err(format!("Type text error: {:?}", e)))?;

    Ok(())
}

#[pyfunction]
#[pyo3(signature = (direction = "down", amount = 3))]
fn scroll(direction: &str, amount: i32) -> PyResult<()> {
    ensure_input_desktop();
    let mut enigo = Enigo::new(&Settings::default())
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to init Enigo: {:?}", e)))?;

    let (axis, amt) = match direction.to_lowercase().as_str() {
        "up" => (Axis::Vertical, -amount),
        "left" => (Axis::Horizontal, -amount),
        "right" => (Axis::Horizontal, amount),
        _ => (Axis::Vertical, amount),
    };

    enigo
        .scroll(amt, axis)
        .map_err(|e| PyRuntimeError::new_err(format!("Scroll error: {:?}", e)))?;

    Ok(())
}

#[pyfunction]
fn press_key(key_name: &str) -> PyResult<()> {
    ensure_input_desktop();
    let mut enigo = Enigo::new(&Settings::default())
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to init Enigo: {:?}", e)))?;

    let key = parse_key(key_name).map_err(PyRuntimeError::new_err)?;

    enigo
        .key(key, Direction::Click)
        .map_err(|e| PyRuntimeError::new_err(format!("Key click error: {:?}", e)))?;

    Ok(())
}

#[pyfunction]
fn hotkey(keys: Vec<String>) -> PyResult<()> {
    ensure_input_desktop();
    let mut enigo = Enigo::new(&Settings::default())
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to init Enigo: {:?}", e)))?;

    let parsed_keys: Vec<Key> = keys
        .iter()
        .map(|k| parse_key(k).map_err(PyRuntimeError::new_err))
        .collect::<PyResult<Vec<_>>>()?;

    for key in &parsed_keys {
        enigo
            .key(*key, Direction::Press)
            .map_err(|e| PyRuntimeError::new_err(format!("Key press error: {:?}", e)))?;
    }

    for key in parsed_keys.iter().rev() {
        enigo
            .key(*key, Direction::Release)
            .map_err(|e| PyRuntimeError::new_err(format!("Key release error: {:?}", e)))?;
    }

    Ok(())
}

#[pymodule]
fn actuator_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_screenshot, m)?)?;
    m.add_function(wrap_pyfunction!(get_screen_dimensions, m)?)?;
    m.add_function(wrap_pyfunction!(click_at, m)?)?;
    m.add_function(wrap_pyfunction!(mouse_move, m)?)?;
    m.add_function(wrap_pyfunction!(type_text, m)?)?;
    m.add_function(wrap_pyfunction!(scroll, m)?)?;
    m.add_function(wrap_pyfunction!(press_key, m)?)?;
    m.add_function(wrap_pyfunction!(hotkey, m)?)?;
    Ok(())
}
