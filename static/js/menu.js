function setupMenu() {
    const menuToggle = document.querySelector('.menu-toggle');
    const menu = document.querySelector('.menu');
    const menuOverlay = document.querySelector('.menu-overlay');
    const menuItems = document.querySelectorAll('.menu-item');

    const setOpen = (open, restoreFocus = false) => {
        menu.classList.toggle('visible', open);
        menuOverlay.classList.toggle('visible', open);
        menuToggle.setAttribute('aria-expanded', String(open));
        menuToggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
        menu.setAttribute('aria-hidden', String(!open));
        if (restoreFocus) {
            menuToggle.focus();
        }
    };

    menuToggle.addEventListener('click', () => {
        setOpen(!menu.classList.contains('visible'));
    });
    menuOverlay.addEventListener('click', () => setOpen(false, true));

    menuItems.forEach(item => {
        item.addEventListener('click', event => {
            if (item.getAttribute('href') === window.location.pathname) {
                event.preventDefault();
                setOpen(false, true);
            }
        });
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && menu.classList.contains('visible')) {
            setOpen(false, true);
            event.stopImmediatePropagation();
        }
    });
}

setupMenu();