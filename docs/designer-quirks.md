# Ampio Designer quirks

## Matter device-type tags that only half exist

The device-type tag on an output ("Description in device" -> for example "Lighting - On-off light") is stored twice: in the output's description record inside the module's CAN memory, and mirrored into the `type` column of the M-SERV's object catalogue. The integration classifies relays from the catalogue column, because the column is served to every account tier. The CAN records answer the admin login only, and an entity's platform must build identically on both tiers (see the stability contract below).

Tags saved with older Ampio tooling exist only in the CAN record, and the catalogue column stayed empty. A relay in that state shows its Lighting tag in Designer, yet Home Assistant surfaces it as a switch. To check what the integration sees for an output, request the object catalogue from the M-SERV as [debugging.md](debugging.md#reading-the-raw-catalogue) describes, and read the `type` field in the object's row.

The fix, in the current web Designer: touch every affected output individually - select a different device type and switch it back to Lighting, so Designer registers an edit - then save once. One save covers all the outputs you touched. Verified behavior on a real install, and confirmed in the Designer web bundle:

- Designer tracks changes per module and per category (a `descriptions` dirty flag on the device), and the save re-sends a dirty module's whole description table over the CAN bus.
- The catalogue column, however, updates only for the outputs you actually edited in the UI. An untouched neighbor keeps its stale column even though its record just went over the wire again - which is why every output needs its own flip, however correct it looks in Designer.
- Designer registers an edit only on a real change, so flip the value away and back. A Lokalizacja change counts too, and the tag rides along with it.

## Unchecking an object's Matter box removes its entity

**Check:** An object's entities are gone from Home Assistant, and a repair on the Settings page titled "Ampio objects with no module output" names the object's id. If you opened that object in Ampio Designer and cleared its "Matter" checkbox, this is why. The same ids appear under `snapshot.not_configured` in the diagnostics download, described in [debugging.md](debugging.md).

Every object row carries a `leafId`, the pointer to the module output that drives it. Designer's per-object "Matter" checkbox rewrites that field on every save. A check writes it back from the linked output record and re-syncs the `type` column from the module record. An uncheck saves the row without it, and the M-SERV stores an empty value.

The object itself survives the uncheck, keeping its type, its rooms, and its state, and Home Assistant loses it all the same. That pointer is how this integration reads and drives an object, and it is where the object's module address comes from, so an object without one gets no entities at all. An object that had entities before keeps its records, its own device included, and the repair titled "Ampio records to clean up", or "Ampio records to check" on a standard account, leaves all of them alone while the object stays in this state, even after you acknowledge this repair.

**Fix:** Open the object in Designer, check its Matter box, and save. An entity that comes back as the same type comes back within seconds with the entity id, the name, and the area it had, because Home Assistant kept its record. A check on a relay also re-syncs the type column from the module record, so a relay whose record lost its Lighting tag comes back as a switch, and one whose record kept the tag comes back as a light (see the section above). Where that puts a relay on the other side, the entity registers under its new type as a new entity, with a new id and no history, and the old record stays behind for the repair titled "Ampio records to clean up", or "Ampio records to check" on a standard account.

So leave the Matter box alone on every object that has an entity here. To stop the M-SERV's Matter bridge, use "Clear configuration" in Designer's Matter panel. That wipes the bridge's pairing and restarts it unpaired, and it touches no object.

If you meant the object to be gone, delete it in Designer instead. The repair drops it on the next catalogue push or the next Home Assistant start, and the repair titled "Ampio records to clean up", or "Ampio records to check" on a standard account, then offers the records it left behind.

## Two modules on one MAC address drop both

**Check:** One or more modules have no name, no model, and no firmware version in Home Assistant, and a press on a control that acts on the module itself, such as its Identify button, its buzzer, or its panel lights, fails with an error saying that no unique module row matches. A repair on the Settings page titled "Ampio modules sharing one MAC address" names each shared address with the Designer device ids that carry it.

Every frame on the Ampio bus is addressed by a module's MAC. Designer's MAC override field lets two devices be saved on one address, and nothing in Designer objects. This integration cannot tell those modules apart, so it leaves them out of its module list. Their objects still work, because an object carries its own address. A module device loses its name, model, and firmware version at the next start or reload of the integration, and until then it keeps what it showed before the MAC became shared. What goes is everything that needs the module's own row. The Identify button, the buzzer, Unlock touch, and the panel lights answer a press with an error saying that no unique module row matches, and the supply voltage and temperature sensors read unknown. Whether the buzzer, Unlock touch, and the panel lights exist at all depends on whether the module answered the record sweep when the integration last started, so they can be missing too.

The M-SERV's own device carries MAC `0x1`, which is also the default a device takes until you give it one, so the usual way into this is a module left on that default. The repair writes every address in hex, the same form Designer's MAC field uses.

**Fix:** Open each device named in the repair in Designer, give it its own MAC address, and save. Match the devices by MAC rather than by position, because Designer numbers the rows of its device list by position (see the section below). Refresh the Designer page afterwards and check that the new MAC stuck, because Designer can drop a MAC edit without saying so.

## Moving an object to another module

Home Assistant cannot move a child device to another parent. When you move an object to another module in Designer, the object's device keeps its old parent. The object's entities stay on that device and keep working. Within seconds the integration logs a warning that names the object and lists the device in the repair on the Settings page. Replacing a module does not do this, because the replacement takes the same MAC address and the object stays where it was.

Submit the repair, or delete the object's device under Settings, then Devices and services. The object's device comes back under the new module, with its id, its area, and its name restored, and its entities come back on it with their entity ids. If that object was the last one on its old module, the old module device stays behind empty, and the repair lists it too.

## Integer sensor slots and the Modbus divider

An M-CON-485 stores each Modbus reading in an integer sensor slot, and Designer turns the slot into a `bit 8`, `bit 16`, `sbit 16`, or `bit 32` object. The slot holds whole numbers only. To keep decimals, do this in Designer:

1. In the Modbus query, multiply the register by a power of ten, for
   example 100 for two decimals.
2. Open the object, click Dictionary, enable Divide by, and enter the same
   factor.
3. Set the Unit field, or a string format that ends with the unit, such as
   `%.2f A`. Click outside the Unit field before you save, because Designer
   commits the field when it loses focus.

The M-SERV applies the divider before it publishes, so the integration receives the real value and adds no scale of its own. The entity reads the unit from the string format tail first, then from the Unit field. Home Assistant's own unit table then gives the device class: `A` reads current, `V` voltage, `W` and `kW` power, `Hz` frequency, and `lx` illuminance. Some units belong to two classes in that table, and the integration settles these:

| Unit                    | Device class                                                                                      |
| ----------------------- | ------------------------------------------------------------------------------------------------- |
| kWh, Wh, MWh            | energy, as a running total                                                                        |
| °C, °F                  | temperature                                                                                       |
| hPa, Pa, kPa, bar, mbar | pressure (the M-SENS barometer reads atmospheric pressure, and a generic slot is not a barometer) |

Any other unit that belongs to two classes, such as `%` or `m³`, keeps the unit and gets no device class. A unit Home Assistant does not know does the same. A slot with no unit at all surfaces as a plain number with no state class, so it keeps no long-term statistics until you give it a unit.

Set the Unit field before the slot gets its entity when you can. For a slot that exists before you install the integration, that means before the install. For a new slot, it means the same Designer save that creates it. Home Assistant takes the unit of a new statistic from the first state in the five-minute window it compiles. A unit set after that state leaves the statistic with none. Such a statistic compiles no further, and the Energy dashboard picker never lists the slot, because the statistic carries no unit class. If the picker does not list a slot, open Settings, then Tools, then Statistics, and fix the unit issue on the entity. Both choices there, delete the old statistics or update the unit, put the slot in the picker.

A unit change in Designer reaches the entity at once. Home Assistant writes the precision hint and the original device class into its registry at registration, so those two follow on the next reload. If you change a unit to another unit of the same quantity, such as `A` to `mA`, Home Assistant keeps showing the unit it registered first and converts the values, because it stores that first unit as the display unit at registration. The statistics convert the same way, so a change like that raises no repair. A change to a unit Home Assistant cannot convert from the old one, such as a unit of another quantity, raises its statistics repair for that entity, because the recorded history carries the old unit. If you set a display unit for the sensor in Home Assistant, it keeps that display unit after such a change. The conversion to it then fails before the state reaches the recorder, and the statistics repair appears only after the integration reloads. A change of the divider rescales the value, and the history keeps the old scale.

The Divide by checkbox is the same mechanism that gives linear inputs their decimals, because Designer creates those with Divide by 10.

## The device list numbers rows by position, not by device id

The first column of Designer's device list is the row's place in the list. It is not the device id the server holds, and it is not what this integration keys a module device on either, which is the module's MAC address.

The two numbers match on an installation where no device was ever deleted, so the difference never shows. Delete a device and the server keeps the id it gave every other device, leaving a gap. Designer's list has no gap, so every device below the deleted one is drawn with a number one lower than its real id. Create a device and fill the gap, and the numbers line up again.

So a number you read off that screen is not the id the server holds. It is also why a delete looks like it renumbered your devices. Nothing was renumbered. Device ids are only ever appended, and a freed id comes back only to a device with the same MAC address as the one that owned it.

This is why the repair for a shared MAC lists the Designer device ids rather than row numbers, and why you should match the devices it names by their MAC address instead of counting down the list.

## Deleting an object takes two passes

Removing an object from a place in Designer does not delete it. It unassigns it, and the object moves to the collapsed **UNGROUPED** section at the bottom. It still exists, it still belongs to its device, and Home Assistant still has its entity. Open UNGROUPED and delete it there to remove it.

Deleting a device behaves differently again: it applies at once, with no save step, and it leaves its objects behind.

Between those two, Designer can leave an object that is still shown to Home Assistant while the device it belongs to is gone. The integration handles it, because an object carries the address of the module that drives it and needs no device row to be filed under. That module device keeps its entities and takes the name `Ampio module <MAC>`, with the address in hex as in `Ampio module 0xCB8F`, since no catalogue row is left to name it. Finish the delete in UNGROUPED and the repair on the Settings page lists what is left over.

## A roller lock stops the slats too

Designer's roller actions "Disable movement", "Disable closing" and "Disable opening" set two lock bits on the channel. The module then drops every command in the blocked direction, with no error and no reply. The wire carries the bits as the `block` field on the cover's state.

The bits also govern the slat axis, which no Ampio document states. Measured on a blind driven by a rule that blocks opening alone:

| Lock            | Command                   | Result             |
| --------------- | ------------------------- | ------------------ |
| None            | Slats 0 to 70             | Runs               |
| Opening blocked | Slats 0 to 70             | Dropped in silence |
| Opening blocked | Slats 70 to 20            | Runs               |
| Opening blocked | Travel 40 to 20           | Runs               |
| Opening blocked | Travel 20 to 60           | Dropped in silence |
| Both blocked    | Slats in either direction | Dropped in silence |

So a slat turn toward open counts as opening, and one toward closed counts as closing. The integration drops each tilt control with the direction that matches it, exactly as it does for the travel.

Designer's two other roller actions, "Close permanently" and "Open permanently", were not measured.

## Designer's messages do not tell you what the server did

All three cases below were seen on **virtual devices**, and each one misleads in a different direction:

- **A virtual device created without an object offers no save button, and reaches the server anyway.** The device is there even though Designer never let you save it.
- **Renaming that device fails, and says so confusingly.** The toast reads **"Device does not exist"** with **"Name updated"** underneath. The title is right and the subtitle is wrong: the name is not saved.
- **Editing its MAC address in place can be dropped with no message at all.** On a real module the MAC edit goes through.

So do not trust the toast in either direction. Refresh the Designer page instead. It asks for confirmation and then asks for the password again, even when the browser has it stored, and what it shows afterwards is what the server holds.

## An analog flag's turn-on time is ignored

Designer's turn-on time column pulses a relay or a flag, and this repo has measured only that. Its editor also offers the same field on a dimmer, the RGB kinds, and a bell object, which is a relay or a flag carrying the bell bit rather than a fourth type. Designer's "Description in device" panel offers the field on an analog flag too, and the M-SERV does not honor it there.

On 2026-09-15, against a live M-SERV, `set_value` on a `flaga_liniowa` carrying a Designer time of 500 (5 seconds) sent `/api/set/<id>/setValue/120/500` on the wire, the same timed form that reverts a relay or a flag. The object still read 120 fifty-three seconds later, with no further state push. A second write of 255 with the same time did not revert after 8 seconds either, so the write reached the module and the module kept the value on this component type regardless of the value written.

The integration's number entity writes the value alone and sends no time, because a time it cannot honor buys nothing and the diagnostic sensor that would otherwise report a pulse length is built only for the types that honor one. Since ampio-mqtt 0.67.0, `AmpioObject.pulse_ms` classifies which kinds a timed write actually pulses and reports 0 for an analog flag instead of echoing the Designer column.

## The stability contract

Ampio accounts upgrade and downgrade between the administrator login and app-created users. The integration therefore derives everything that defines an entity's platform or the device topology from data the restricted tier receives.

An entity id is built once, when Home Assistant first registers the entity, by default from the area name, the device name and the entity name, leaving out any part that is empty. After that it holds still until you ask Home Assistant to regenerate it, for one entity from its settings page or for several from a selection in the entities table, or until you follow the reset procedure in [faq.md](faq.md). Rename a device, move it to another area, or switch the account tier, and every id stays where it is.

The names are what keep that true. An object's entity takes the name of the object's own device, which is the name you gave the object in the Ampio app and which both account tiers receive, and it takes the area seeded from the app room, which both tiers receive as well. A module's own name comes from the admin-only module catalogue, and it reaches an id only through the entities that sit on a module device, which exist on the administrator login alone. So a tier change moves no id, and no module entity is registered under a name the account is not served. A downgraded install keeps the module records the administrator login registered until you submit the "Ampio entities for administrators only" repair that lists them.

A module takes the name you gave it in Ampio Designer where the admin-only module catalogue answers, and falls back to `Ampio module <MAC>` on a restricted account, with the address in hex as in `Ampio module 0xCB8F`. The hub is always `M-SERV`. The catalogue also decorates the module's model, the firmware and hardware versions, and the serial number. All of those follow the account tier, so a tier change renames a module in the interface and moves nothing else.

A module's MAC is what its device is keyed on, and both account tiers receive it, because every object carries the address of the module that drives it. So no tier change moves a device. Neither does replacing a module, because Designer stamps the same MAC onto the replacement unit, and the device and its entities carry on as if nothing happened, even though the replacement holds a new position in Designer's device list. Home Assistant cannot move a child device to another parent, so the one thing that does move an object between modules is a Designer edit followed by the delete described above.
