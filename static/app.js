const dispatcher_status_elements = {
    on_air_studio: $('#status_on_air_studio'),
    state: $('#status_state'),
    x: $('#status_x'),
    y: $('#status_y'),
};

let proto = 'ws://';
if (location.protocol === 'https:') {
    proto = 'wss://';
}
const status_ws_url = proto + location.host + '/api/v1/ws';

let led_map = {};

let selected_studio = '';

const update_selected_studio = function (new_value) {
    if (new_value === '') {
        selected_studio = null;
    } else {
        selected_studio = new_value;
        connection.send(JSON.stringify({type: 'studio.led.status', studio: selected_studio}));
    }
    console.log(selected_studio);
};

const update_led_status = function (payload) {
    if (payload.studio in led_map) {
        let led_entry = led_map[payload.studio];
        for (let color in payload.status) {
            led_entry[color][0].dataset.state = payload.status[color].state.toLowerCase();
            led_entry[color][0].style.animationDuration = (1 / payload.status[color].blink_freq) + 's';
        }
    }
};

$('.studio-button').click(function (event) {
    if (selected_studio) {
        let url = location.protocol + '//' + location.host + '/api/v1/' + selected_studio + '/press/' + event.target.dataset.kind;
        $.get(url);
    }
});

$('#studio_selector').change(function (event) {
    update_selected_studio(event.target.value);
});

let graph_url = null;
const graph_container = $('#graph_container');
const graph_buttons = $('.graph-buttons');

let change_graph_url = function (new_url) {
    graph_url = new_url;
    console.log(graph_url);
    graph_container.empty();
    if (graph_url != null) {
        graph_container.append(
            $('<img alt="graph" src="' + graph_url + '?' + Math.random() + '">')
        );
    }
    graph_buttons.each(function (_, el) {
        let $el = $(el);
        if (el.dataset.img === graph_url) {
            $el.addClass('btn-primary');
            $el.removeClass('btn-secondary');
        } else {
            $el.removeClass('btn-primary');
            $el.addClass('btn-secondary');
        }
    })
};

graph_buttons.click(function (event) {
    change_graph_url(event.target.dataset.img);
});

$.get(
    '/api/v1/studios',
    function (data) {
        const studio_selector = $('#studio_selector');
        studio_selector.empty();
        studio_selector.append($('<option></option>'));

        const leds_table = $('#leds');
        let row_template = $('tr', leds_table).clone();
        leds_table.empty();

        data.forEach(function (studio) {
            studio_selector.append(
                $('<option value="' + studio + '">' + studio + '</option>')
            );

            let led_entry = {
                'row': row_template.clone()
            };
            ['name', 'green', 'yellow', 'red'].forEach(function (key) {
                led_entry[key] = $('#' + key, led_entry['row']);
                led_entry[key].removeAttr('id');
            });
            led_entry['name'].text(studio);

            leds_table.append(led_entry['row']);
            led_map[studio] = led_entry;

            if (connection !== null && connection.readyState === WebSocket.OPEN) {
                connection.send(JSON.stringify({type: 'studio.led.status', studio: studio}));
            }
        });
    }
);

let connection = null;

function connection_start() {
    connection = new WebSocket(status_ws_url);

    // When the connection is open, send some data to the server
    connection.onopen = function () {
        update_selected_studio($('#studio_selector').val());
    };

// Log errors
    connection.onerror = function (error) {
        console.log('WebSocket Error ' + error);
    };

    connection.onclose = function (arg) {
        console.log('WebSocket Close ' + arg);
        setTimeout(function () {
            connection_start()
        }, 10000);
    };

// Log messages from the server
    connection.onmessage = function (e) {
        const data = JSON.parse(e.data);

        switch (data.kind) {
            case 'dispatcher.status':
                dispatcher_status_elements.on_air_studio.text(data.payload.on_air_studio);
                dispatcher_status_elements.state.text(data.payload.state);
                dispatcher_status_elements.x.text(data.payload.x);
                dispatcher_status_elements.y.text(data.payload.y);
                break;
            case 'studio.led.status':
                const payload = data.payload;
                update_led_status(payload);
                break;
            default:
                console.log(data);
        }
    };
}

connection_start();
