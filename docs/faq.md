# Frequently asked questions

If something looks wrong after an update or after a change in Ampio Designer, find the symptom below. Each answer tells you how to check whether it affects you, and how to fix it.

## Back up first

Do not start a procedure on this page without a backup. Some of them delete records that Home Assistant cannot rebuild from the Ampio server.

1. Open Settings, then System, then Backups.
2. Select "Create backup".
3. Wait for the backup to finish.

## Entities are missing or stay unavailable after an update

**Check:** Open Settings, then Devices and services, then Ampio. Compare the entity count with what you had. An entity with the state `unavailable` or the label "restored" is one the integration no longer builds.

**Fix:** Remove the Ampio integration entry, then add it again. Home Assistant remembers a removed entity for 30 days, so the re-add restores your entity ids, your renames, and your areas. That is the supported first step, not a last resort. If the entity count is still wrong afterwards, download the diagnostics as described in [debugging.md](debugging.md) and report it in the issues.

## Setup does not finish, and stays on "Retrying setup"

**Check:** Open Settings, then Devices and services. The Ampio entry shows "Retrying setup" instead of its usual state, and the reason underneath reads "Connected to the Ampio server, but it did not answer the module description request." This affects the administrator login only, because a standard account never sends that request.

**Fix:** None is required. The integration retries the request on its own, waiting longer between each attempt, and finishes loading as soon as the M-SERV answers. If it stays stuck for several minutes, check whether the M-SERV itself is slow to respond, mid-restart, or overloaded, and give it time to recover. If the entry still will not finish after that, download the diagnostics as described in [debugging.md](debugging.md) and report it in the issues.

## An entity is listed that Ampio Designer no longer has

**Check:** Such an entity shows the state `unavailable` or the label "restored". After each start or reload the integration raises up to two repairs on the Settings page, under Repairs. One lists the devices and entities it did not build and cannot explain. The other lists the entities it withheld because your Ampio account is not the administrator one.

**Fix:** Select Submit on the repair to delete them all at once. The integration then reloads. An object that you moved to another module in Ampio Designer comes back under its new module.

The repair never deletes on its own. On an account that is not the administrator one, an object that lost its app permission looks the same as a deleted object. Read the list before you submit.

To delete a single record by hand instead:

1. Open Settings, then Devices and services, then Entities.
2. Search for the entity.
3. Select it, then select the cog icon.
4. Select "Delete".

If the "Delete" button is not offered, the integration still creates that entity. Check Ampio Designer before you go further.

## I changed my Ampio password

Home Assistant notices on its own. The entry stops, and a notification asks you to sign in again. Open it, enter the new password, and submit.

To change the password before it breaks, open the entry, choose Reconfigure from its menu, and enter the new one.

## I want to use a different Ampio account, or I replaced my M-SERV

Open the entry, choose Reconfigure from its menu, and enter the address and the credentials to use. Your devices and your entities keep their ids, their areas, and any name you gave them yourself.

Do not delete the integration for this. A delete removes every device record and every entity record. You lose every rename and every area with them.

A different account changes what the server serves you. An app-created user receives the objects granted to it in the Ampio app, so an object outside that grant loses its entities. The repair on the Settings page lists them. Read the list before you submit it. If you move to a standard account, a second repair lists the administrator-only entities it withheld, such as the Identify buttons, the module sensors, the buzzer, the Unlock touch buttons, and the Backlight and Status light entities, and that one is explained under "The administrator-only entities are gone".

If the address you enter answers with different M-SERV hardware, the flow asks you to confirm first. It names the CAN address it found. Continue only if you replaced the M-SERV, or if you meant to point Home Assistant at another one.

## My entity ids look different from the ones in the docs

**Check:** Open Settings, then Devices and services, then Entities, and search for `ampio`. The current form is `<domain>.ampio_obj_<object id>` for an object's entity, for example `light.ampio_obj_7`, or `<domain>.ampio_module_<row>_<name>` for a module's, for example `button.ampio_module_12_identify`. An install that predates a change of the form keeps its older ids.

**Fix:** None is required. Both forms work, and nothing forces you to change. A release can change the form for a fresh install, and every existing install keeps the ids it has. If you want the current form on an existing install, use the reset procedure below. It is a support procedure, and no update requires it.

## Why does an entity id never change on its own?

Home Assistant builds an entity id once, when the entity registers for the first time. After that the id is stored, and no later change moves it. A device rename does not move it. An area change does not move it. An integration update does not move it.

Removing the integration does not move it either. Home Assistant remembers a removed entity for 30 days. If you add the integration again inside that window, the old entity id comes back. The name you gave the entity and the area you put it in come back with it.

This is good behavior. Your automations keep working across an update, a rename, and a reinstall.

## A relay I tagged as a light shows as a switch

See [designer-quirks.md](designer-quirks.md). That page tells you how to check the tag in the diagnostics, and how to re-save the output in Designer.

## An object sits under the wrong module

See [designer-quirks.md](designer-quirks.md). That page explains why Home Assistant cannot move a child device, and how the repair puts the object under its new module.

## A cover's arrow is missing, every control is gone, or an automation that names it fails

**Check:** On the dashboard, the cover card offers only the arrow for the direction that still works and drops the other one, with no toast and no notification explaining why, or the card offers no control at all and reports only the position. An automation that names the cover by its entity id fails instead, with a message such as "Entity cover.ampio_obj_82 does not support action cover.open_cover."

A rule in Ampio Designer, or a lock the `ampio.set_roller_lock` action set, is locking the cover's travel. A Designer rule holds the lock through one of the roller actions, "Disable movement", "Disable closing", or "Disable opening", for as long as its trigger holds, so a wind alarm or a fire alarm can hold it for a long time, and the lock clears on its own once the trigger clears. A lock the action set holds until something releases it. The cover's Opening lock and Closing lock diagnostic binary sensors report which direction, if any, is currently held.

The module drops the blocked command with no error and no reply, so the integration removes the control instead of letting a press do nothing. The cover keeps reporting its position the whole time. The position slider still works in the direction that is not locked, and refuses the other with a message naming the `Ampio: Set roller lock` action and the Designer rule that can hold it. `cover.toggle` needs both directions, so locking either one disables it entirely.

The lock covers the slats as well. On a blind, the tilt arrows and the tilt slider follow the same two directions as the travel, so a lock on opening also stops a slat turn toward open and leaves a turn toward closed working.

A third cause takes every control at once, arrows, slider, and both stop buttons together, and the tell is exactly that completeness. A lock in both directions still leaves the stop buttons working, because a stop is not a move and the module never blocks it. If the stops are gone too, the object is marked read-only in Ampio Designer, and the server refuses every verb behind it, on every axis, so there is nothing left for this integration to offer.

An administrator can still hold or release this cover's roller lock through the `ampio.set_roller_lock` action while it is read-only, because the lock write rides the raw tree rather than the path the read-only marker gates. Doing so changes nothing about the cover itself. Only the lock binary sensors' own state moves, since the read-only refusal already applies above where the lock bits are read. A scene that captured this cover before it went read-only stops restoring it, silently, with no warning and no error.

**Fix:** For a lock, none is required. The arrow returns once the lock clears, whether that is a Designer rule's trigger or a call to `ampio.set_roller_lock` releasing it. For read-only, clear the checkbox in Ampio Designer if you want the cover to take commands again. An automation that targets the cover through an area, a device, or a label skips it silently while either cause holds. An automation that names the cover by its entity id raises and halts the rest of the sequence unless the action sets `continue_on_error: true`.

## A cover's roller lock binary sensor reads off, and the set roller lock action fails

**Check:** Each cover carries an Opening lock and a Closing lock diagnostic binary sensor, both under the cover device's Diagnostic section, and both read the module's own lock bits on either account tier.

Calling `ampio.set_roller_lock` against a cover on the administrator login can raise an error naming the unsupported module, instead of doing nothing. An older module generation accepts the same lock frame as an ordinary roller move and drops it without changing anything, so it advertises no roller channel count in its capability map. A module that stayed silent during the setup sweep raises the same error, because a lock write still needs the module's own answer to size its frame. Either way the two binary sensors read `off` and stay there, because the bit they read never moves.

On a standard account the action raises an error naming the account tier before it reaches the module at all, whether or not that module supports the lock.

**Fix:** For the generation gap, none is required. That module never gains the lock, and its two binary sensors read `off` for good. For a module that stayed silent during the sweep, reloading the integration may help, because that runs the sweep again. On a standard account, ask whoever holds the administrator login to set or release the lock.

A lock the action sets never expires on its own. Whichever account sets one owns releasing it: call the action again with `blocked` off, or clear it from wherever it was set, such as an automation. A Designer wind or fire alarm rule clears its lock when its trigger clears, and does not re-assert one you released underneath it, so releasing that lock with the action leaves the cover free to move until the rule fires again.

## A warm/cold white light's color temperature does not match my strip

The slider works, and the numbers are a scale rather than a measurement.

Ampio serves a warm/cold white output as two raw bytes, a power level and a coldness level, each 0 to 255. The M-SERV publishes no kelvin range for such an object, and its `min` and `max` columns read 0 and 255. So nothing on the wire says what white 3512 K is.

The integration maps the coldness byte onto Home Assistant's own default range, 2000 K at the warmest end and 6535 K at the coldest. The ends of the slider reach the ends of your strip. A number in the middle is a position on that scale rather than a measured temperature, so a strip sold as 2700 K to 6000 K shows numbers that run wider than its specification.

Every position on the slider is stable. The same number always gives the same white, and an automation or a scene that stores one reproduces it.

## My analog flag ignores the turn-on time I set in Designer

Ampio Designer offers a turn-on time on an analog flag, the same column its editor offers on a relay, a flag, a dimmer, and the RGB kinds. This repo has measured the pulse itself only on a relay and a flag. Home Assistant does not send it here, because the M-SERV does not apply it to this object type. A measurement against a live M-SERV, on 2026-09-15, wrote a value to an analog flag with that time attached, and the flag still held the written value fifty-three seconds later, with no reversion. See [designer-quirks.md](designer-quirks.md) for the numbers behind it.

The value you write to the number entity stays where you wrote it until something writes another one. If you want a value to fall back after a delay, build that timing in an automation, because Designer's turn-on time field does nothing for this object type.

## A read-only object still shows a Pulse time reading

**Check:** Open Settings, then Devices and services, then Entities, and find the object's Pulse time diagnostic sensor. If the switch, button, or light beside it refuses every command with an error naming the read-only marker, open the object in Ampio Designer and confirm its read-only checkbox is set.

Ampio Designer's read-only checkbox blocks a write to the object at the server, on both account tiers, not only from Home Assistant. A relay, a flag, or a light marked read-only keeps its Pulse time diagnostic sensor, and the sensor keeps reporting whatever turn-on time Designer stores for it, even though the server drops every write to the object and that time never actually times anything. The reading is accurate, and it reports the time a turn-on write would carry if the checkbox were cleared.

**Fix:** None is required. Clear the read-only checkbox in Ampio Designer to let the object accept writes again. The pulse time then applies to the next turn-on the same as it would for any object with a Designer time.

## A module shows no last-seen time in the diagnostics

**Check:** Find the module's row under `snapshot.modules` in the diagnostics download, as described in [debugging.md](debugging.md). Look at `supply_voltage` in the same row. If that value is empty too, the module sends no health broadcast, and its last-seen time moves only when one of its objects changes. On the reference install the roller modules, the M-SERV row, and an M-CON-s on old firmware send none. The library lists every type and firmware in [raw-channel-bridge.md](https://github.com/pszypowicz/ampio-mqtt/blob/main/docs/raw-channel-bridge.md). A module that shows a voltage does send the broadcast. Its last-seen time can still lag by a few minutes after a restart, because the broadcast comes on a change of the reading only.

**Fix:** None is required. An empty value says only that nothing arrived from that module since the restart. Move one of its covers, or wait for one of its objects to change, and the row fills. If a light or a cover on that module still responds, the module is alive.

## My touch panels go dark when I turn off all the lights

**Check:** Read the automation or the script that turns the lights off. Two forms of it reach a panel's Backlight and Status light:

- A template over `states.light` whose result is passed as `entity_id`.
- A call to `light.turn_off` with `entity_id: all`.

Every other way to target lights skips them already. Both entities carry the diagnostic category, and Home Assistant leaves an entity with a category out of area, device, and floor targeting, out of the voice assistants, and out of the HomeKit bridge.

The two forms above are different. A template over `states.light` sees every light entity, and no template test reports an entity's category. A list of entity ids is a direct target, and a direct target is never filtered.

**Fix:** Reject the panel entities inside the template. The entity ids are pinned, so this pattern holds:

```jinja
{{ states.light
   | rejectattr('entity_id', 'search', '^light[.]ampio_module_[0-9]+_(backlight|status_light)$')
   | map(attribute='entity_id') | list }}
```

A label works too. Apply one label to both entities of each panel, then reject `label_entities('<your label>')`. A label survives a rename of an entity id, and it needs one more step for each panel you add later.

For `entity_id: all`, name your targets instead. Home Assistant turns off every light entity for that value, and the category changes nothing.

The same reject belongs in every template that reads `states.light`. A sensor that counts the lights that are on, and a card that lists them, both include two entities per panel without it.

## The administrator-only entities are gone

Several module entities are provided with the administrator login alone: the Identify button, the supply voltage and temperature sensors, the buzzer on a module with a touch panel, that panel's Unlock touch button, and its Backlight and Status light. The Ampio server carries the frames and the readings behind them to that login and no other, so a standard account is given none of them.

The Identify button once existed on both accounts, where a press on a standard account only ever returned an error. If you did not change anything, an update is what removed it. If you changed the account this integration uses, that removed it too. The sensors, the buzzer, the Unlock touch button, and the Backlight and Status light are administrator-only from their first release, so a standard account never had them to lose.

A repair on the Settings page lists whichever of these entities your account withholds. Submit it to delete the records, or leave it alone. If you point the integration back at the administrator account, the entities come back on their own with the same entity ids.

## My module devices are named "Ampio module 12" now

Only on a standard Ampio account. The names you gave your modules in Ampio Designer are served to the administrator login alone, so an update replaced a name built from the module's address with its row number in Designer. That row is where the module was named in the first place, and it is still the same device you have always had.

A name you typed yourself in Home Assistant is untouched, and no entity id moved. On an administrator account nothing changed.

## How do I reset every Ampio entity id?

Use this to move an existing install onto the current id form. The procedure deletes every Ampio entity record, so Home Assistant builds the ids again from scratch on the next start.

**Every automation, script, scene, and dashboard card that names an Ampio entity id stops working.** Write those ids down first, and plan to repoint them.

You need shell access to the Home Assistant host, through the SSH add-on or the Terminal add-on.

1. Take a backup, as described above.
2. Write down the Ampio entity ids your automations use.
3. Stop Home Assistant:

   ```sh
   ha core stop
   ```

4. Copy the entity registry, then remove every Ampio record from it:

   ```sh
   sudo cp /config/.storage/core.entity_registry /config/.storage/core.entity_registry.bak
   sudo jq '(.data.entities, .data.deleted_entities) |= map(select(.platform != "ampio"))' \
     /config/.storage/core.entity_registry > /tmp/registry.json
   sudo cp /tmp/registry.json /config/.storage/core.entity_registry
   ```

5. Start Home Assistant:

   ```sh
   ha core start
   ```

6. Open Settings, then Devices and services, then Ampio. Confirm that the entity count matches what you had.
7. Repoint your automations at the new ids.

The `deleted_entities` list matters as much as the `entities` list. Leave the deleted records in place, and Home Assistant restores every old id on the next start.

### If something goes wrong

Stop Home Assistant, copy the backup file back, then start Home Assistant:

```sh
ha core stop
sudo cp /config/.storage/core.entity_registry.bak /config/.storage/core.entity_registry
ha core start
```

If the instance does not start at all, restore the full backup from Settings, System, Backups.

## What this does not touch

- Your Ampio configuration. The M-SERV holds it, and this integration only reads it.
- Your devices. Device names and areas live in a separate registry.
- Any other integration. The filter selects the `ampio` platform alone.
