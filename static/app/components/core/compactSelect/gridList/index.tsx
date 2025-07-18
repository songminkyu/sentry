import {Fragment, useContext, useId, useMemo, useRef} from 'react';
import type {AriaGridListOptions} from '@react-aria/gridlist';
import {useGridList} from '@react-aria/gridlist';
import {mergeProps} from '@react-aria/utils';
import type {ListState} from '@react-stately/list';
import type {CollectionChildren} from '@react-types/shared';

import {SelectContext} from 'sentry/components/core/compactSelect/control';
import {SelectFilterContext} from 'sentry/components/core/compactSelect/list';
import {
  ListLabel,
  ListSeparator,
  ListWrap,
  SizeLimitMessage,
} from 'sentry/components/core/compactSelect/styles';
import type {SelectKey, SelectSection} from 'sentry/components/core/compactSelect/types';
import {t} from 'sentry/locale';

import {GridListOption, type GridListOptionProps} from './option';
import {GridListSection} from './section';

interface GridListProps
  extends Omit<React.HTMLAttributes<HTMLUListElement>, 'children'>,
    Omit<
      AriaGridListOptions<any>,
      | 'disabledKeys'
      | 'selectedKeys'
      | 'defaultSelectedKeys'
      | 'onSelectionChange'
      | 'autoFocus'
    > {
  /**
   * Keyboard event handler, to be attached to the list (`ul`) element, to seamlessly
   * move focus from one composite list to another when an arrow key is pressed. Returns
   * a boolean indicating whether the keyboard event was intercepted. If yes, then no
   * further callback function should be run.
   */
  keyDownHandler: (e: React.KeyboardEvent<HTMLUListElement>) => boolean;
  /**
   * Object containing the selection state and focus position, needed for
   * `useGridList()`.
   */
  listState: ListState<any>;
  children?: CollectionChildren<any>;
  /**
   * Text label to be rendered as heading on top of grid list.
   */
  label?: React.ReactNode;
  /**
   * To be called when the user toggle-selects a whole section (applicable when sections
   * have `showToggleAllButton` set to true.) Note: this will be called in addition to
   * and before `onChange`.
   */
  onSectionToggle?: (
    section: SelectSection<SelectKey>,
    type: 'select' | 'unselect'
  ) => void;
  size?: GridListOptionProps['size'];
  /**
   * Message to be displayed when some options are hidden due to `sizeLimit`.
   */
  sizeLimitMessage?: string;
}

/**
 * A grid list with accessibile behaviors & attributes.
 * https://react-spectrum.adobe.com/react-aria/useGridList.html
 *
 * Unlike list boxes, grid lists are two-dimensional. Users can press Arrow Up/Down to
 * move between rows (options), and Arrow Left/Right to move between "columns". This is
 * useful when the select options have smaller, interactive elements (buttons/links)
 * inside. Grid lists allow users to focus on those child elements (using the Arrow
 * Left/Right keys) and interact with them, which isn't possible with list boxes.
 */
function GridList({
  listState,
  size = 'md',
  label,
  onSectionToggle,
  sizeLimitMessage,
  keyDownHandler,
  ...props
}: GridListProps) {
  const ref = useRef<HTMLUListElement>(null);
  const labelId = useId();
  const {gridProps} = useGridList(
    {...props, 'aria-labelledby': label ? labelId : props['aria-labelledby']},
    listState,
    ref
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLUListElement>) => {
    const continueCallback = keyDownHandler?.(e);
    // Prevent grid list from clearing value on Escape key press
    if (continueCallback && e.key !== 'Escape') {
      gridProps.onKeyDown?.(e);
    }
  };

  const {overlayIsOpen, search} = useContext(SelectContext);
  const hiddenOptions = useContext(SelectFilterContext);
  const listItems = useMemo(
    () =>
      [...listState.collection].filter(node => {
        if (node.type === 'section') {
          return ![...node.childNodes].every(child => hiddenOptions.has(child.key));
        }

        return !hiddenOptions.has(node.key);
      }),
    [listState.collection, hiddenOptions]
  );

  return (
    <Fragment>
      {listItems.length !== 0 && <ListSeparator role="separator" />}
      {listItems.length !== 0 && label && <ListLabel id={labelId}>{label}</ListLabel>}
      <ListWrap {...mergeProps(gridProps, props)} onKeyDown={onKeyDown} ref={ref}>
        {overlayIsOpen &&
          listItems.map(item => {
            if (item.type === 'section') {
              return (
                <GridListSection
                  key={item.key}
                  node={item}
                  listState={listState}
                  onToggle={onSectionToggle}
                  size={size}
                />
              );
            }

            return (
              <GridListOption
                key={item.key}
                node={item}
                listState={listState}
                size={size}
              />
            );
          })}

        {!search && hiddenOptions.size > 0 && (
          <SizeLimitMessage>
            {sizeLimitMessage ?? t('Use search to find more options…')}
          </SizeLimitMessage>
        )}
      </ListWrap>
    </Fragment>
  );
}

export {GridList};
